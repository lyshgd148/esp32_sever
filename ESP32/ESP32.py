"""
ESP32 MicroPython OTA 接收端
============================
帧格式: [0xAA] [0x55] [CMD:1B] [SEQ:2B BE] [LEN:2B BE] [PAYLOAD:LEN] [CRC16:2B BE]
OTA 期间暂停文本协议 (PING/PONG/DATA), 结束后自动恢复
"""

import network
import socket
import time
import machine
import neopixel
import gc

pin = machine.Pin(48, machine.Pin.OUT)

# ===== 1颗灯 =====
np = neopixel.NeoPixel(pin, 1)

# ================= 配置 =================
SSID = "CMCC-204"
PASSWORD = "18015116492"

PC_IP = "192.168.10.107"
PC_PORT = 9000

sock = None
send_enable = False
motor_state = "STOP"
flash_active = False  # STM32 烧录进行中, 暂停心跳和数据流

np[0] = (255, 0, 0)
np.write()

# ================= CRC 预计算 =================
# CRC16 查表, 多项式 0x1021 (CRC-16/XMODEM), 帧级校验
_CRC16_TABLE = []  # 256 元素, 每项 16-bit
for _i in range(256):
    _crc = _i << 8
    for _ in range(8):
        if _crc & 0x8000:
            _crc = ((_crc << 1) ^ 0x1021) & 0xFFFF
        else:
            _crc = (_crc << 1) & 0xFFFF
    _CRC16_TABLE.append(_crc)


def crc16(data):
    """计算字节序列 CRC16 (MicroPython 没有 struct, 手动迭代)"""
    crc = 0
    for b in data:
        crc = ((crc << 8) ^ _CRC16_TABLE[((crc >> 8) ^ b) & 0xFF]) & 0xFFFF
    return crc


# CRC32 查表, 反射多项式 0xEDB88320 (CRC-32/gzip), 文件级校验
_CRC32_TABLE = []  # 256 元素, 每项 32-bit
for _i in range(256):
    _crc = _i
    for _ in range(8):
        if _crc & 1:
            _crc = (_crc >> 1) ^ 0xEDB88320
        else:
            _crc = _crc >> 1
    _CRC32_TABLE.append(_crc)


def crc32_init():
    """初始 CRC32 值"""
    return 0xFFFFFFFF


def crc32_update(crc, data):
    """增量更新 CRC32, 每接收一个 chunk 调用一次"""
    for b in data:
        crc = _CRC32_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc


def crc32_final(crc):
    """CRC32 最终取反"""
    return crc ^ 0xFFFFFFFF


# ================= WiFi =================
def wifi_connect():
    while True:
        wlan = network.WLAN(network.STA_IF)

        wlan.active(False)
        time.sleep(1)
        wlan.active(True)

        wlan.disconnect()
        time.sleep(1)

        print("Connecting WiFi...")
        wlan.connect(SSID, PASSWORD)

        timeout = 0
        while not wlan.isconnected():
            time.sleep(0.5)
            timeout += 1
            if timeout > 40:
                print("WiFi failed, retry...")
                break

        if wlan.isconnected():
            print("WiFi OK:", wlan.ifconfig())
            return wlan


# ================= TCP连接 =================
def tcp_connect():
    global sock, send_enable

    try:
        if sock:
            sock.close()
    except:
        pass

    sock = None

    fail_count = 0

    while True:
        try:
            wlan = network.WLAN(network.STA_IF)
            if not wlan.isconnected():
                print("WiFi lost -> reconnecting")
                wifi_connect()

            print("Connecting TCP...")
            s = socket.socket()
            try:
                s.settimeout(5)
                s.connect((PC_IP, PC_PORT))
                s.settimeout(0.1)
                sock = s
                send_enable = False
                print("TCP connected")
                return
            except:
                try:
                    s.close()
                except:
                    pass
                raise

        except Exception as e:
            fail_count += 1
            print("TCP fail #%d: %s" % (fail_count, e))

            if fail_count % 10 == 0:
                gc.collect()

            if fail_count > 100:
                print("Too many failures -> machine reset")
                machine.reset()

            time.sleep(3)


# ================= 安全发送 =================
def safe_send(msg):
    global sock

    try:
        if sock:
            sock.send(msg.encode())
            return True
    except:
        print("send failed -> reconnect TCP")
        tcp_connect()
    return False


# ================= 二进制帧发送 =================
# OTA 帧格式常量
MAGIC = b"\xaa\x55"
CMD_OTA_START = 0x10  # S→E
CMD_OTA_DATA = 0x11
CMD_OTA_ABORT = 0x12
CMD_OTA_ACK = 0x20  # E→S
CMD_OTA_DONE = 0x21
CMD_OTA_ERROR = 0x22
CHUNK_SIZE = 256


def send_binary_frame(cmd, seq, payload):
    """
    构建并发送一帧二进制数据:
      [MAGIC 2B] [CMD 1B] [SEQ 2B BE] [LEN 2B BE] [PAYLOAD] [CRC16 2B BE]
    """
    plen = len(payload)
    # 构建帧体 (CMD~PAYLOAD) 用于 CRC 校验
    head = bytes([cmd, (seq >> 8) & 0xFF, seq & 0xFF, (plen >> 8) & 0xFF, plen & 0xFF])
    body = head + payload
    crc = crc16(body)
    frame = MAGIC + body + bytes([(crc >> 8) & 0xFF, crc & 0xFF])
    try:
        if sock:
            sock.send(frame)
    except:
        print("send binary frame failed")


# ================= 模拟编码器 =================
i, j = 0.0, 0.0


def read_encoder():
    global i, j
    i += 2.2
    j += 1.1
    if i > 360:
        i = 0.0
    if j > 380:
        j = 0.0

    return i, j


def send_state():
    a, s = read_encoder()
    safe_send(f"STATE,motor={motor_state},angle={a},speed={s}\n")


def send_data():
    a, s = read_encoder()
    safe_send(f"DATA,angle={a},speed={s}\n")
    time.sleep(0.1)


# ================= OTA 状态机 =================
# ota_state 取值:
#   0 = 文本模式 (默认)
#   1 = 等待 OTA_START 帧 (已回复 OTA_READY)
#   2 = 接收数据块中
#   3 = 校验 CRC32 中
ota_state = 0
ota_crc32 = 0  # 运行中的 CRC32 (增量)
ota_filesize = 0  # 预期文件大小
ota_received = 0  # 已连续接收的字节数 (用于 ACK)
ota_file = None  # 文件句柄
ota_last_seen = {}  # 记录已写 chunk 的 seq, 用于去重


def ota_start_ready():
    """收到 OTA_START 文本命令 → 准备接收"""
    global ota_state, ota_crc32, ota_filesize, ota_received, ota_file, ota_last_seen
    print("OTA: start ready")
    ota_state = 1
    ota_crc32 = crc32_init()
    ota_filesize = 0
    ota_received = 0
    ota_last_seen = {}
    # 打开文件准备写入
    try:
        ota_file = open("./template.bin", "wb")
    except Exception as e:
        print("OTA: open file failed", e)
        ota_state = 0
        safe_send("OTA_ERROR\n")
        return
    # 回复就绪
    safe_send("OTA_READY\n")


def handle_ota_frame(cmd, seq, payload):
    """
    处理收到的二进制帧 (OTA 模式下所有通信都走这里)
    返回: need_reply (是否已回复, 调用方据此决定是否继续解析文本)
    """
    global ota_state, ota_crc32, ota_filesize, ota_received, ota_file, ota_last_seen

    if cmd == CMD_OTA_START:
        # 解析文件大小和预期 CRC32
        if len(payload) < 8:
            send_binary_frame(CMD_OTA_ERROR, 0, bytes([2]))  # 2=payload异常
            ota_state = 0
            return True
        fsize = payload[0] | (payload[1] << 8) | (payload[2] << 16) | (payload[3] << 24)
        expected_crc = (
            payload[4] | (payload[5] << 8) | (payload[6] << 16) | (payload[7] << 24)
        )
        ota_filesize = fsize
        ota_crc32 = crc32_init()
        ota_received = 0
        ota_last_seen = {}
        ota_state = 2  # 进入接收模式
        print("OTA: file_size=%d, crc32=0x%08X" % (fsize, expected_crc))
        # ACK: 当前已连续接收 0 字节
        send_binary_frame(CMD_OTA_ACK, 0, bytes([0, 0, 0, 0]))
        return True

    elif cmd == CMD_OTA_DATA:
        if ota_state != 2:
            return True
        if len(payload) < 4:
            return True
        # 解析 chunk 偏移
        offset = (
            payload[0] | (payload[1] << 8) | (payload[2] << 16) | (payload[3] << 24)
        )
        chunk_data = payload[4:]
        chunk_len = len(chunk_data)

        # 跳过重复 chunk
        if seq in ota_last_seen:
            # 仍然回复 ACK (可能之前的 ACK 丢了)
            send_binary_frame(CMD_OTA_ACK, 0, _pack_u32(ota_received))
            return True
        ota_last_seen[seq] = True

        # 写入文件 (随机位置)
        try:
            ota_file.seek(offset)
            ota_file.write(chunk_data)
        except Exception as e:
            print("OTA: write error", e)
            send_binary_frame(CMD_OTA_ERROR, 0, bytes([3]))  # 3=写错误
            ota_file.close()
            ota_state = 0
            return True

        # 更新增量 CRC32
        ota_crc32 = crc32_update(ota_crc32, chunk_data)

        # 更新连续接收计数器
        if offset == ota_received:
            ota_received += chunk_len
            # 扫描: 后续连续的 chunk 是否已到达 (乱序场景)
            while True:
                if ota_received >= ota_filesize:
                    break
                _nc = ota_received // CHUNK_SIZE  # 下一个 chunk 索引
                if _nc in ota_last_seen:
                    ota_received = min(ota_received + CHUNK_SIZE, ota_filesize)
                else:
                    break

        # 无需每块都发 ACK: 累积 ACK 即可
        # 但每次收到都发 ACK 方便服务器统计
        send_binary_frame(CMD_OTA_ACK, 0, _pack_u32(min(ota_received, ota_filesize)))

        # 检查是否收完
        if ota_received >= ota_filesize:
            ota_state = 3
            print("OTA: all chunks received, verifying CRC32 ...")
            ota_verify()
        return True

    elif cmd == CMD_OTA_ABORT:
        print("OTA: aborted by server")
        if ota_state >= 1:
            _ota_cleanup()
        ota_state = 0
        return True

    return False


def _pack_u32(val):
    """打包 4 字节小端整数"""
    return bytes(
        [val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF, (val >> 24) & 0xFF]
    )


def _pack_u16(val):
    """打包 2 字节小端整数"""
    return bytes([val & 0xFF, (val >> 8) & 0xFF])


def ota_verify():
    """所有数据已接收, 校验 CRC32 并回复结果"""
    global ota_state, ota_crc32, ota_filesize
    final_crc = crc32_final(ota_crc32)
    print("OTA: received_crc32=0x%08X" % final_crc)
    # 发送 OTA_DONE 帧
    # payload = status(0=OK) + received_crc32(4B LE)
    payload = bytes([0]) + _pack_u32(final_crc)
    send_binary_frame(CMD_OTA_DONE, 0, payload)
    # 清理, 回到文本模式
    _ota_cleanup()
    ota_state = 0
    print("OTA: done")


def _ota_cleanup():
    """清理 OTA 相关资源"""
    global ota_file
    try:
        if ota_file:
            ota_file.close()
    except:
        pass
    ota_file = None


def _do_flash():
    """收到 FLASH_BIN 命令 → 同步执行 STM32 烧录 (阻塞主循环, 无线程)"""
    global flash_active
    flash_active = True
    try:
        from esp32sever.STM32DOWN import STM32Loader

        def _cb(step, *args):
            if step == "start":
                safe_send("FLASH_START=%d\n" % args[0])
            elif step == "erase":
                safe_send("FLASH_ERASE\n")
            elif step == "chunk":
                idx, total = args
                pct = idx * 100 // total if total else 0
                safe_send("FLASH_PROGRESS=%d\n" % pct)
            elif step == "done":
                safe_send("FLASH_DONE\n")
            elif step == "error":
                safe_send("FLASH_ERROR=%s\n" % args[0])

        loader = STM32Loader()
        loader.flash("template.bin", callback=_cb)
    except Exception as e:
        safe_send("FLASH_ERROR=%s\n" % str(e))
    finally:
        flash_active = False


def do_ir_learn():
    """收到 LEARN_IR 命令 → 录制红外时序并回传"""
    try:
        from esp32sever.infrared import IRLearner

        learner = IRLearner()
        data = learner.record(5000)
        gc.collect()
        if data:
            safe_send("IR_DATA=" + ",".join(str(v) for v in data) + "\n")
        else:
            safe_send("IR_ERROR=超时, 未收到红外信号\n")
    except Exception as e:
        gc.collect()
        safe_send("IR_ERROR=%s\n" % str(e))


def do_ir_send(data_str):
    """解析红外时序并发射"""
    try:
        from esp32sever.infrared import IRLearner

        data = [int(x) for x in data_str.split(",") if x]
        IRLearner().send_raw(data)
        gc.collect()
        safe_send("IR_SEND_OK\n")
    except Exception as e:
        gc.collect()
        safe_send("IR_SEND_ERROR=%s\n" % str(e))


# ================= 主循环 =================
def loop():
    global send_enable, motor_state, sock, ota_state, flash_active

    wifi_connect()
    tcp_connect()

    # ping and pong 心跳
    # ping 每 8s 发送一次, 服务器每 12s 发送一次 pong
    # ESP32发送ping接收pong pong未超时，证明服务器正常
    last_ping = time.ticks_ms()
    last_pong = time.ticks_ms()

    recv_buf = b""  # TCP 接收缓冲区
    _prev_ota = 0  # 上次 OTA 状态, 用于检测模式切换
    _was_paused = False  # 刚才是否处于 OTA/Flash 暂停态, 恢复时重置 PONG 超时

    while True:
        try:
            now = time.ticks_ms()

            # ===== PING/PONG 心跳 (OTA/Flash 期间暂停) =====
            if ota_state == 0 and not flash_active:
                if _was_paused:
                    last_pong = now  # 刚从暂停恢复, 防止虚假超时
                _was_paused = False

                if time.ticks_diff(now, last_ping) > 10000:  # 2s → 8s
                    safe_send("PING\n")
                    last_ping = now

                if time.ticks_diff(now, last_pong) > 15000:  # 6s → 12s
                    print("No PONG -> reconnect")
                    tcp_connect()
                    last_pong = time.ticks_ms()
                    continue
            else:
                _was_paused = True

            # ================= 接收数据 =================
            try:
                data = sock.recv(1024)

                if not data:
                    print("TCP lost -> reconnect")
                    tcp_connect()
                    last_pong = time.ticks_ms()
                    recv_buf = b""
                    continue

                if data:
                    recv_buf += data

                    # ---- OTA 二进制帧解析 ----
                    if ota_state > 0:
                        # 尝试从缓冲区头部解析二进制帧
                        while (
                            len(recv_buf) >= 9
                        ):  # 最小帧 9 字节(魔术2+命令1+序号2+长度2+负载0+CRC2)
                            if recv_buf[0] != 0xAA or recv_buf[1] != 0x55:
                                # 跳过非帧头字节
                                recv_buf = recv_buf[1:]
                                continue
                            cmd = recv_buf[2]
                            seq = (recv_buf[3] << 8) | recv_buf[4]
                            plen = (recv_buf[5] << 8) | recv_buf[6]
                            frame_len = 9 + plen
                            if len(recv_buf) < frame_len:
                                break  # 帧还不完整, 等下次 recv
                            # 校验 CRC16
                            crc_area = recv_buf[2 : 7 + plen]
                            exp_crc = crc16(crc_area)
                            recv_crc = (recv_buf[7 + plen] << 8) | recv_buf[
                                7 + plen + 1
                            ]
                            if exp_crc != recv_crc:
                                # CRC 错误, 丢弃魔术头第一字节后重试
                                recv_buf = recv_buf[1:]
                                continue
                            payload = recv_buf[7 : 7 + plen]
                            handle_ota_frame(cmd, seq, payload)
                            recv_buf = recv_buf[frame_len:]
                        _prev_ota = ota_state

                    # ---- 文本模式解析 ----
                    else:
                        # 检测刚从 OTA 退出, 清空缓冲区避免二进制残留
                        if _prev_ota > 0:
                            recv_buf = b""
                        while b"\n" in recv_buf:
                            idx = recv_buf.find(b"\n")
                            line = recv_buf[:idx].decode().strip()
                            recv_buf = recv_buf[idx + 1 :]
                            if not line:
                                continue

                            # ===== PONG =====
                            if line == "PONG":
                                last_pong = time.ticks_ms()
                                continue

                            # ===== OTA 准备 =====
                            if line == "OTA_START":
                                ota_start_ready()
                                continue

                            # ===== Flash 烧录 =====
                            if line == "FLASH_BIN":
                                _do_flash()
                                continue

                            # ===== 红外学习 =====
                            if line == "LEARN_IR":
                                do_ir_learn()
                                continue

                            # ===== 红外发射 =====
                            if line.startswith("SEND_IR="):
                                data_str = line.split("=", 1)[1]
                                do_ir_send(data_str)
                                continue

                            # ===== 状态请求 =====
                            if line == "GET_STATE":
                                send_state()

                            # ===== 数据流控制 =====
                            elif line == "START_DATA":
                                send_enable = True

                            elif line == "STOP_DATA":
                                send_enable = False

                            # ===== 电机控制 =====
                            elif line == "MOTOR_START":
                                np[0] = (0, 255, 0)
                                np.write()
                                motor_state = "START"
                                safe_send("MOTOR=START\n")

                            elif line == "MOTOR_STOP":
                                np[0] = (255, 0, 0)
                                np.write()
                                motor_state = "STOP"
                                safe_send("MOTOR=STOP\n")

                            elif line.startswith("COLOR="):
                                _, rgb = line.split("=")
                                r, g, b = rgb.split(",")
                                np[0] = (int(r), int(g), int(b))
                                np.write()
                        _prev_ota = ota_state

            except OSError:
                pass  # timeout正常，不算断线

            # ================= 数据流 =================
            if send_enable and ota_state == 0 and not flash_active:
                send_data()

            gc.collect()
            time.sleep(0.1)

        except Exception as e:
            print("fatal -> reconnect everything", e)
            wifi_connect()
            tcp_connect()
            last_pong = time.ticks_ms()
            recv_buf = b""
            ota_state = 0
            flash_active = False
            _ota_cleanup()


# ================= 启动 =================
loop()
        