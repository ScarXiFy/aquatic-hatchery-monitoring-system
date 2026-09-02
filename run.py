from app import create_app, socketio
from app.api.piUtils import cleanupGpio

app = create_app()


if __name__ == "__main__":
    try:
        socketio.run(app, host="0.0.0.0", port=5050, debug=True, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        pass
    finally:
        cleanupGpio()
