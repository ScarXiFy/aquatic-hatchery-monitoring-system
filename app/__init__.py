from flask import Flask, request
from flask_socketio import SocketIO

from app.config import Config
from app.database import close_db, init_db

socketio = SocketIO(cors_allowed_origins="*", async_mode="threading")


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    init_db(app)
    app.teardown_appcontext(close_db)

    from app.api.routes import api_bp
    from app.main.routes import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    socketio.init_app(app)

    with app.app_context():
        from app.socketio_events import register_socketio_events, notify_client_connected

        register_socketio_events(socketio)

    @app.before_request
    def check_active_browser_client():
        if request.environ.get("werkzeug.test"):
            return
        user_agent = request.headers.get("User-Agent", "").lower()
        if "python-urllib" in user_agent:
            return
        if "mozilla" in user_agent or request.path.startswith("/socket.io"):
            notify_client_connected()

    return app
