import threading
import time
from flask import current_app, request

from app.sensors.simulator import start_sensor_simulator
from app.api.piUtils import initializeBleedValveMotor, initializeValveMotorStates, initializeLed, mainValve

SERVER_BOOT_TIME = time.time()
_simulator_started = False
connected_clients = set()
local_connected_clients = set()
client_connected_event = threading.Event()
local_client_connected_event = threading.Event()


def is_local_remote_addr(remote_addr):
    """Returns True if remote_addr refers to the local machine (loopback)."""
    if not remote_addr:
        return False
    addr = str(remote_addr).strip().lower()
    if addr in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"):
        return True
    if addr.startswith("127.") or addr.startswith("::ffff:127."):
        return True
    return False


def notify_client_connected(remote_addr=None, is_local=None):
    """Signals that a web browser client has connected or sent a request."""
    client_connected_event.set()
    if is_local is None:
        is_local = True if remote_addr is None else is_local_remote_addr(remote_addr)
    if is_local:
        local_client_connected_event.set()


def reset_client_connected_state():
    """Resets client tracking (primarily for testing or cleanup)."""
    connected_clients.clear()
    local_connected_clients.clear()
    client_connected_event.clear()
    local_client_connected_event.clear()


def is_client_connected(local_only=False):
    """Returns True if at least one client (or local client when local_only=True) is connected."""
    if local_only:
        return len(local_connected_clients) > 0 or local_client_connected_event.is_set()
    return len(connected_clients) > 0 or client_connected_event.is_set()


def wait_for_client_connection(timeout=2.0, local_only=False):
    """
    Waits up to `timeout` seconds for a web client to connect or be detected.
    When `local_only=True`, ignores remote LAN clients and only detects clients
    connected from the local machine (loopback).
    Returns True immediately if a matching client is already connected.
    """
    if is_client_connected(local_only=local_only):
        return True
    event = local_client_connected_event if local_only else client_connected_event
    return event.wait(timeout=timeout)


def register_socketio_events(socketio):
    @socketio.on("connect")
    def handle_connect():
        sid = getattr(request, "sid", None) or "client"
        remote_addr = getattr(request, "remote_addr", None)
        local = is_local_remote_addr(remote_addr)
        connected_clients.add(sid)
        if local:
            local_connected_clients.add(sid)
        notify_client_connected(is_local=local)
        socketio.emit("server_ready", {"boot_id": SERVER_BOOT_TIME}, to=sid)
        try:
            from app.models import get_control_settings, get_thresholds
            from app.api.routes import CONTROL_STATE, SETPOINT_UPDATED_AT, IS_SYSTEM_START, SETPOINT_ADJUSTING
            sliders = get_control_settings()
            thresholds_data = get_thresholds()
            socketio.emit("controls_updated", {
                "sliders": sliders,
                "valves": CONTROL_STATE.get("valves", {}),
                "setpoint_times": SETPOINT_UPDATED_AT,
                "is_system_start": IS_SYSTEM_START,
                "adjusting_state": SETPOINT_ADJUSTING,
                "server_time": int(time.time() * 1000),
            }, to=sid)
            socketio.emit("thresholds_updated", {
                "thresholds": thresholds_data
            }, to=sid)
            from app.api.piUtils import get_maintenance_state
            socketio.emit("maintenance_updated", get_maintenance_state(), to=sid)
        except Exception:
            pass
        start_background_simulator(socketio)

    @socketio.on("disconnect")
    def handle_disconnect():
        sid = getattr(request, "sid", None) or "client"
        connected_clients.discard(sid)
        local_connected_clients.discard(sid)
        if not connected_clients:
            client_connected_event.clear()
        if not local_connected_clients:
            local_client_connected_event.clear()


def start_background_simulator(socketio):
    global _simulator_started
    if _simulator_started:
        return

    _simulator_started = True
    app = current_app._get_current_object()
    initializeBleedValveMotor(app)
    initializeValveMotorStates(app)
    initializeLed(app)
    mainValve(True)
    socketio.start_background_task(
        start_sensor_simulator,
        socketio,
        app,
    )

