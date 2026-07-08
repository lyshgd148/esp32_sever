from flask import Blueprint, request, render_template, redirect, session
from .. import state as st
from ..utils.db import fetch_one

account_bp = Blueprint("account", __name__)


@account_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    telephone = request.form.get("telephone", "").strip()
    password = request.form.get("password", "").strip()

    if not telephone or not password:
        return render_template("login.html", error="请输入手机号和密码")

    user = fetch_one(
        "SELECT id, telephone, name FROM users WHERE telephone=%s AND password=%s",
        [telephone, password],
    )
    if user:
        session.permanent = True
        session["user_info"] = {
            "id": user[0],
            "telephone": user[1],
            "name": user[2],
        }
        return redirect("/")

    return render_template("login.html", error="手机号或密码错误")


@account_bp.route("/logout")
def logout():
    session.clear()
    st.safe_send(b"STOP_DATA\n")
    return redirect("/login")
