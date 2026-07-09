import os
import threading
from flask import Blueprint, render_template, request, jsonify
from .. import state as st
from ..extensions import socketio
from ..ota import OtaSender

upload_bp = Blueprint("upload", __name__)

DEST_DIR = os.path.join(os.path.dirname(__file__), "..", "BIN")
DEST_FILE = "template.bin"
MAX_SIZE = 1048576


@upload_bp.route("/upload", methods=["GET"])
def upload_page():
    return render_template("upload.html")


@upload_bp.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"message": "未选择文件。"}), 400

    f = request.files["file"]

    if f.filename == "":
        return jsonify({"message": "未选择文件。"}), 400

    if not f.filename.lower().endswith(".bin"):
        return jsonify({"message": "仅支持 .bin 文件。"}), 400

    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)

    if size > MAX_SIZE:
        return jsonify({"message": "文件大小超过 1MB。"}), 400

    os.makedirs(DEST_DIR, exist_ok=True)
    dest_path = os.path.join(DEST_DIR, DEST_FILE)
    f.save(dest_path)

    return jsonify({"message": "上传成功，已保存为 template.bin。"}), 200


# ================= OTA 上传 =================
@upload_bp.route("/upload/ota", methods=["POST"])
def start_ota():
    """触发 ESP32 OTA, 将 app/BIN/template.bin 发送到 ESP32"""
    if not st.esp32_conn:
        return jsonify({"message": "ESP32 未连接。"}), 400

    if not st.try_start_ota():
        return jsonify({"message": "另一操作正在进行中, 请稍候。"}), 409

    dest_path = os.path.join(DEST_DIR, DEST_FILE)
    if not os.path.exists(dest_path):
        st.end_ota()
        return jsonify({"message": "文件 template.bin 不存在, 请先上传到服务器。"}), 400

    # 在后台线程中运行 OTA, 避免阻塞 Flask 响应
    def _run_ota():
        sender = OtaSender(dest_path)
        try:
            sender.run()
        except Exception as e:
            socketio.emit(
                "ota_progress", {"percent": -1, "message": "OTA 异常: " + str(e)}
            )
        finally:
            st.end_ota()

    threading.Thread(target=_run_ota, daemon=True).start()
    return jsonify({"message": "OTA 已启动"}), 200


# ================= Flash 烧录 =================
@upload_bp.route("/upload/flash", methods=["POST"])
def start_flash():
    """触发 ESP32 烧录 STM32, 发送 FLASH_BIN 命令"""
    if not st.esp32_conn:
        return jsonify({"message": "ESP32 未连接。"}), 400

    if not st.try_start_flash():
        return jsonify({"message": "另一操作正在进行中, 请稍候。"}), 409

    st.safe_send(b"FLASH_BIN\n")
    return jsonify({"message": "FLASH_BIN 已发送"}), 200
