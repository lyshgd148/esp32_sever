import time
from .extensions import socketio
from . import state as st
from .state import safe_send

_index_sids = {}


@socketio.on("connect")
def on_connect(auth=None):
    from flask import request
    if request.args.get("page") == "index":
        _index_sids[request.sid] = True
        st.web_count += 1
        st.web_online = True
        st.last_heartbeat = time.time()
        print("WEB ONLINE")
        safe_send(b"GET_STATE\n")
        safe_send(b"START_DATA\n")
        socketio.sleep(0.5)
    socketio.emit("state", st.state)
    socketio.emit("web_count", {"count": st.web_count})


@socketio.on("disconnect")
def on_disconnect():
    from flask import request
    if request.sid in _index_sids:
        del _index_sids[request.sid]
        st.web_count -= 1
        if st.web_count <= 0:
            st.web_count = 0
            st.web_online = False
            safe_send(b"STOP_DATA\n")
        print("count:", st.web_count)
        print("WEB OFFLINE")
    socketio.emit("web_count", {"count": st.web_count})


@socketio.on("color")
def on_color(rgb):
    if not st.esp32_conn:
        return
    safe_send(f"COLOR={rgb['r']},{rgb['g']},{rgb['b']}\n".encode())


@socketio.on("motor")
def motor(cmd):
    if not st.esp32_conn:
        return

    if cmd["cmd"] == "START":
        safe_send(b"MOTOR_START\n")

    elif cmd["cmd"] == "STOP":
        safe_send(b"MOTOR_STOP\n")


@socketio.on("heartbeat")
def heartbeat():
    st.last_heartbeat = time.time()


@socketio.on("ir_learn")
def on_ir_learn(data):
    device = data.get("device", "").strip()
    key = data.get("key", "").strip()

    if not device or not key:
        socketio.emit("ir_error", {"message": "设备名和按键名不能为空"})
        return

    if not st.esp32_conn:
        socketio.emit("ir_error", {"message": "ESP32 未连接"})
        return

    if not st.try_start_ir_learn():
        socketio.emit("ir_error", {"message": "另一操作正在进行中, 请稍候。"})
        return

    st.pending_ir_context = {"device": device, "key": key}
    st.safe_send(b"LEARN_IR\n")


@socketio.on("ir_send")
def on_ir_send(data):
    device = data.get("device", "").strip()
    key = data.get("key", "").strip()

    if not device or not key:
        socketio.emit("ir_send_error", {"message": "设备名和按键名不能为空"})
        return

    if not st.esp32_conn:
        socketio.emit("ir_send_error", {"message": "ESP32 未连接"})
        return

    if not st.try_start_ir_send():
        socketio.emit("ir_send_error", {"message": "另一操作正在进行中, 请稍候。"})
        return

    ir = st.read_ir()
    raw = ir.get(device, {}).get(key)
    if not raw:
        st.end_ir_send()
        socketio.emit("ir_send_error", {"message": "未找到该按键的红外数据"})
        return

    st.pending_ir_send_context = {"device": device, "key": key}
    st.safe_send(("SEND_IR=" + ",".join(str(v) for v in raw) + "\n").encode())
