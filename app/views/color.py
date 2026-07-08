from flask import Blueprint, render_template

color_bp = Blueprint("color", __name__)


@color_bp.route("/color")
def color():
    return render_template("color.html")
