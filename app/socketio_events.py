from flask import current_app

from app.sensors.simulator import start_sensor_simulator

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
    socketio.start_background_task(
        start_sensor_simulator,
        socketio,
        current_app._get_current_object(),
    )
