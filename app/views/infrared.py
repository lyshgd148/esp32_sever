from flask import Blueprint, render_template, request, jsonify
from .. import state as st

infrared_bp = Blueprint("infrared", __name__)


@infrared_bp.route("/infrared", methods=["GET"])
def infrared_page():
    return render_template("infrared.html")


@infrared_bp.route("/infrared/data", methods=["GET"])
def infrared_data():
    data = st.read_ir()
    return jsonify(data)


@infrared_bp.route("/infrared/delete", methods=["POST"])
def infrared_delete():
    body = request.get_json(silent=True) or {}
    device = body.get("device", "").strip()
    key = body.get("key", "").strip() or None

    if not device:
        return jsonify({"message": "设备名不能为空"}), 400

    st.delete_ir(device, key)
    return jsonify({"message": "删除成功"}), 200
