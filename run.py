import argparse
from app import create_app
from app.extensions import socketio

app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--ipv6", action="store_true")
    args = parser.parse_args()

    host = "::" if args.ipv6 else "0.0.0.0"
    socketio.run(app, host=host, port=args.port)
