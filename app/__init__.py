import threading
import time
from datetime import timedelta
from flask import Flask, request, redirect, session
from .extensions import socketio
from . import state as st
from .tcp import tcp_server


def auth():
    if request.path.startswith("/static"):
        return
    if request.path.startswith("/socket.io"):
        return
    if request.path == "/login" or request.path == "/logout":
        return
    if session.get("user_info"):
        return
    return redirect("/login")


def heartbeat_watchdog():
    while True:
        time.sleep(1)

        timeout = (time.time() - st.last_heartbeat) > 5

        if timeout:
            st.miss_count += 1
        else:
            st.miss_count = 0

        if st.miss_count >= 3 and st.web_online:
            print("WEB OFFLINE -> STOP DATA")
            st.web_online = False
            st.safe_send(b"STOP_DATA\n")

        if not timeout and not st.web_online and st.web_count > 0:
            print("WEB ONLINE -> START DATA")
            st.web_online = True
            st.safe_send(b"START_DATA\n")


def create_app():
    app = Flask(__name__)
    app.secret_key = "985211tianwanggaidihuwoshi250320180151189736tgdha92ha87sa9h21jw9eda9dhaksdh29edayhdaksdh298e"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

    socketio.init_app(app)

    app.before_request(auth)

    @app.context_processor
    def inject_user():
        user_info = session.get("user_info")
        return {"user_name": user_info["name"] if user_info else None}

    from .views.main import main_bp
    from .views.color import color_bp
    from .views.upload import upload_bp
    from .views.infrared import infrared_bp
    from .views.remote import remote_bp
    from .views.account import account_bp
    from .views.audio import audio_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(color_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(infrared_bp)
    app.register_blueprint(remote_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(audio_bp)

    from . import events  # noqa: F811
    from . import ota      # noqa: F811

    _ = events

    threading.Thread(target=tcp_server, daemon=True).start()
    threading.Thread(target=heartbeat_watchdog, daemon=True).start()

    return app
