from flask import Blueprint, render_template

remote_bp = Blueprint("remote", __name__)


@remote_bp.route("/ir_send", methods=["GET"])
def remote_page():
    return render_template("remote.html")
