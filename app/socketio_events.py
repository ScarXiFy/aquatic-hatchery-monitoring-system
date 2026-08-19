from flask import current_app

from app.sensors.simulator import start_sensor_simulator
from app.api.piUtils import initializeBleedValveMotor

_simulator_started = False


def register_socketio_events(socketio):
    @socketio.on("connect")
    def handle_connect():
        start_background_simulator(socketio)


def start_background_simulator(socketio):
    global _simulator_started
    if _simulator_started:
        return

    _simulator_started = True
    app = current_app._get_current_object()
    initializeBleedValveMotor(app)
    socketio.start_background_task(
        start_sensor_simulator,
        socketio,
        app,
    )

