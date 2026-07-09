from flask import Blueprint, render_template

audio_bp = Blueprint("audio", __name__)


@audio_bp.route("/audio")
def audio_page():
    return render_template("audio.html")
