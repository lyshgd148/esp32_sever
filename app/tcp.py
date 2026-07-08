import socket
import threading
import queue
from . import state as st
from .extensions import socketio
from .ota import parse_frame, MAGIC_B0, MAGIC_B1


def tcp_recv_loop(conn):
    """
    TCP 接收循环, 同时支持文本协议和 OTA 二进制帧.
    通过 st.ota_active 标志自动切换解析模式:
      - ota_active=False: 按 \\n 分割文本行, 调用 handle_esp32
      - ota_active=True:  解析二进制帧, 放入 st.ota_queue 供 OtaSender 消费
    """
    conn.settimeout(0.5)
    buf = b""
    prev_ota = False  # 追踪 OTA 模式切换, 用于清空缓冲区
    try:
        while True:
            try:
                data = conn.recv(1024)
                if not data:
                    print("ESP32 disconnected")
                    break

                # OTA 模式切换时清空残存缓冲区, 避免文本/二进制混杂
                if prev_ota != bool(st.ota_active):
                    buf = b""
                    prev_ota = bool(st.ota_active)

                buf += data

                if st.ota_active and st.ota_queue is not None:
                    # ---- 二进制帧模式 ----
                    while True:
                        # 跳过非魔术头字节
                        while len(buf) >= 2 and (
                            buf[0] != MAGIC_B0 or buf[1] != MAGIC_B1
                        ):
                            buf = buf[1:]

                        result = parse_frame(buf)
                        if result is None:
                            # 帧不完整或 CRC 错, 等下次 recv
                            # 如果是 CRC 错导致的不完整缓冲区, 跳过 1 字节
                            if len(buf) >= 9:
                                # 有至少一帧的长度但未解析成功, 可能是中间有垃圾字节
                                # 尝试跳过 1 字节后重试
                                if buf[0] != MAGIC_B0:
                                    break  # 没有找到魔术头, 等更多数据
                                # 魔术头正确但解析失败 → CRC 错 → 跳过魔术头再试
                                buf = buf[1:]
                            break

                        cmd, seq, payload, consumed = result
                        try:
                            st.ota_queue.put((cmd, seq, payload), timeout=1)
                        except queue.Full:
                            pass  # 队列满则丢弃, 优先保证不阻塞 tcp_recv_loop
                        buf = buf[consumed:]

                else:
                    # ---- 文本模式 ----
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = line.decode().strip()
                        if msg:
                            handle_esp32(msg)

            except socket.timeout:
                continue

            except:
                break
    finally:
        conn.close()
        if st.esp32_conn is conn:
            st.esp32_conn = None
        if st.flash_active:
            st.end_flash()
            socketio.emit(
                "flash_progress", {"percent": -1, "message": "ESP32 连接丢失"}
            )
        if st.ota_active:
            st.end_ota()
            socketio.emit("ota_progress", {"percent": -1, "message": "ESP32 连接丢失"})
        if st.ir_learn_active:
            st.end_ir_learn()
            socketio.emit("ir_error", {"message": "ESP32 连接丢失"})
        if st.ir_send_active:
            st.end_ir_send()
            socketio.emit("ir_send_error", {"message": "ESP32 连接丢失"})


def tcp_server():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 9000))
    s.listen(5)

    print("TCP server started")

    while True:
        conn, addr = s.accept()
        print("ESP32 connected:", addr)

        st.esp32_conn = conn

        threading.Thread(target=tcp_recv_loop, args=(conn,), daemon=True).start()
        if st.web_online and st.web_count > 0:
            st.safe_send(b"GET_STATE\n")
            st.safe_send(b"START_DATA\n")


def handle_esp32(msg):
    if msg == "PING":
        st.safe_send(b"PONG\n")
        return

    if msg == "OTA_READY":
        # ESP32 已准备好接收 OTA, 创建帧队列切换到二进制模式
        st.ota_queue = queue.Queue()
        return

    if msg.startswith("DATA"):
        parts = msg.split(",")

        for p in parts:
            if "angle=" in p:
                st.state["angle"] = float(p.split("=")[1])
            if "speed=" in p:
                st.state["speed"] = float(p.split("=")[1])

        socketio.emit("data", st.state)

    elif msg.startswith("MOTOR"):
        st.state["motor"] = msg.split("=")[1]
        socketio.emit("state", st.state)

    elif msg.startswith("STATE"):
        parts = msg.split(",")

        for p in parts:
            if "motor=" in p:
                st.state["motor"] = p.split("=")[1]
            if "angle=" in p:
                st.state["angle"] = float(p.split("=")[1])
            if "speed=" in p:
                st.state["speed"] = float(p.split("=")[1])

        socketio.emit("state", st.state)

    # ================= Flash 烧录状态 =================
    elif msg.startswith("FLASH_START="):
        total = int(msg.split("=")[1])
        socketio.emit(
            "flash_progress", {"percent": 0, "message": "开始烧录 (%d 块)" % total}
        )

    elif msg == "FLASH_ERASE":
        socketio.emit("flash_progress", {"percent": 0, "message": "擦除中 ..."})

    elif msg.startswith("FLASH_PROGRESS="):
        pct = int(msg.split("=")[1])
        socketio.emit(
            "flash_progress", {"percent": pct, "message": "烧录中 %d%%" % pct}
        )

    elif msg == "FLASH_DONE":
        st.end_flash()
        socketio.emit("flash_progress", {"percent": 100, "message": "烧录完成"})

    elif msg.startswith("FLASH_ERROR="):
        st.end_flash()
        err = msg.split("=", 1)[1]
        socketio.emit("flash_progress", {"percent": -1, "message": err})

    # ================= 红外学习 =================
    elif msg.startswith("IR_DATA="):
        data_str = msg.split("=", 1)[1]
        data = [int(x) for x in data_str.split(",") if x]
        ctx = st.pending_ir_context
        if ctx:
            st.save_ir(ctx["device"], ctx["key"], data)
        st.end_ir_learn()
        socketio.emit(
            "ir_done",
            {
                "device": ctx["device"] if ctx else "",
                "key": ctx["key"] if ctx else "",
                "data": data,
            },
        )

    elif msg.startswith("IR_ERROR="):
        err = msg.split("=", 1)[1]
        st.end_ir_learn()
        socketio.emit("ir_error", {"message": err})

    # ================= 红外发射 =================
    elif msg == "IR_SEND_OK":
        ctx = st.pending_ir_send_context
        st.end_ir_send()
        socketio.emit(
            "ir_send_done",
            {
                "device": ctx["device"] if ctx else "",
                "key": ctx["key"] if ctx else "",
            },
        )

    elif msg.startswith("IR_SEND_ERROR="):
        err = msg.split("=", 1)[1]
        st.end_ir_send()
        socketio.emit("ir_send_error", {"message": err})
