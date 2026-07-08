import json
import os
import threading

state = {"angle": 0.0, "speed": 0.0, "motor": "UNKNOWN"}

esp32_conn = None

last_heartbeat = 0
web_online = False
web_count = 0
miss_count = 0

# OTA 固件上传
ota_active = False  # True: TCP 连接进入二进制帧模式, 暂停文本协议
ota_queue = None  # queue.Queue, tcp_recv_loop 把解析好的二进制帧放这里
ota_progress = 0  # 0~100, 前端进度条用

# Flash 烧录 (ESP32 侧阻塞执行, 服务端只跟踪状态用于互斥)
flash_active = False

# 红外学习
ir_learn_active = False
pending_ir_context = None  # {"device": "电视", "key": "电源"}

# 红外发射
ir_send_active = False
pending_ir_send_context = None  # {"device": "电视", "key": "电源"}

_op_lock = threading.Lock()

# infrared.json 路径与读写锁
_IR_JSON_PATH = os.path.join(os.path.dirname(__file__), "infrared.json")
_ir_json_lock = threading.RLock()


def try_start_ota():
    """原子检查+置位: 返回 True 抢占成功, False 已有其他操作进行中"""
    global ota_active
    with _op_lock:
        if ota_active or flash_active or ir_learn_active or ir_send_active:
            return False
        ota_active = True
        ota_queue = None
        return True


def try_start_flash():
    """原子检查+置位: 返回 True 抢占成功, False 已有其他操作进行中"""
    global flash_active
    with _op_lock:
        if ota_active or flash_active or ir_learn_active or ir_send_active:
            return False
        flash_active = True
        return True


def try_start_ir_learn():
    """原子检查+置位: 返回 True 抢占成功, False 已有其他操作进行中"""
    global ir_learn_active
    with _op_lock:
        if ota_active or flash_active or ir_learn_active or ir_send_active:
            return False
        ir_learn_active = True
        return True


def try_start_ir_send():
    """原子检查+置位: 返回 True 抢占成功, False 已有其他操作进行中"""
    global ir_send_active
    with _op_lock:
        if ota_active or flash_active or ir_learn_active or ir_send_active:
            return False
        ir_send_active = True
        return True


def end_ota():
    """结束 OTA, 恢复文本模式"""
    global ota_active, ota_queue
    with _op_lock:
        ota_active = False
        ota_queue = None


def end_flash():
    """结束 Flash 烧录"""
    global flash_active
    with _op_lock:
        flash_active = False


def end_ir_learn():
    """结束红外学习"""
    global ir_learn_active, pending_ir_context
    with _op_lock:
        ir_learn_active = False
        pending_ir_context = None


def end_ir_send():
    """结束红外发射"""
    global ir_send_active, pending_ir_send_context
    with _op_lock:
        ir_send_active = False
        pending_ir_send_context = None


# ===== infrared.json 读写 =====


def read_ir():
    """读取 infrared.json, 返回 dict"""
    with _ir_json_lock:
        try:
            with open(_IR_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


def save_ir(device, key, data):
    """保存红外时序: infrared.json[device][key] = data"""
    with _ir_json_lock:
        ir = read_ir()
        if device not in ir:
            ir[device] = {}
        ir[device][key] = data
        os.makedirs(os.path.dirname(_IR_JSON_PATH), exist_ok=True)
        with open(_IR_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(ir, f, ensure_ascii=False, indent=2)


def delete_ir(device, key=None):
    """删除设备或按键: key=None 则删除整个设备"""
    with _ir_json_lock:
        ir = read_ir()
        if key:
            if device in ir:
                ir[device].pop(key, None)
                if not ir[device]:
                    del ir[device]
        else:
            ir.pop(device, None)
        with open(_IR_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(ir, f, ensure_ascii=False, indent=2)


def safe_send(msg):
    """发送字节数据到 ESP32, 失败则清空连接"""
    global esp32_conn
    if not esp32_conn:
        return
    try:
        esp32_conn.send(msg)
    except:
        print("TCP send failed -> drop connection")
        try:
            esp32_conn.close()
        except:
            pass
        esp32_conn = None
