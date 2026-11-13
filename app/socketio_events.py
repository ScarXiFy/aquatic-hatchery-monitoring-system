import threading
import time
from flask import current_app, request

from app.sensors.simulator import start_sensor_simulator
from app.api.piUtils import initializeBleedValveMotor, initializeLed, mainValve

SERVER_BOOT_TIME = time.time()
_simulator_started = False
connected_clients = set()
client_connected_event = threading.Event()


def notify_client_connected():
    """Signals that a web browser client has connected or sent a request."""
    client_connected_event.set()


def reset_client_connected_state():
    """Resets client tracking (primarily for testing or cleanup)."""
    connected_clients.clear()
    client_connected_event.clear()


def is_client_connected():
    """Returns True if at least one client is actively connected."""
    return len(connected_clients) > 0 or client_connected_event.is_set()


def wait_for_client_connection(timeout=2.0):
    """
    Waits up to `timeout` seconds for a web client to connect or be detected.
    Returns True immediately if a client is already connected.
    """
    if is_client_connected():
        return True
    return client_connected_event.wait(timeout=timeout)


def register_socketio_events(socketio):
    @socketio.on("connect")
    def handle_connect():
        sid = getattr(request, "sid", None) or "client"
        connected_clients.add(sid)
        notify_client_connected()
        socketio.emit("server_ready", {"boot_id": SERVER_BOOT_TIME}, to=sid)
        start_background_simulator(socketio)

    @socketio.on("disconnect")
    def handle_disconnect():
        sid = getattr(request, "sid", None) or "client"
        connected_clients.discard(sid)
        if not connected_clients:
            client_connected_event.clear()


def start_background_simulator(socketio):
    global _simulator_started
    if _simulator_started:
        return

    _simulator_started = True
    app = current_app._get_current_object()
    initializeBleedValveMotor(app)
    initializeLed(app)
    mainValve(True)
    socketio.start_background_task(
        start_sensor_simulator,
        socketio,
        app,
    )

