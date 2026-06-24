import random
from datetime import datetime, timezone

from app.models import create_sensor_reading
from app.api.piUtils import applyTemperatureControl, applyDissolvedOxygenControl
from app.api.routes import CONTROL_STATE


def generate_sensor_reading():
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "temperature": round(random.uniform(25.0, 29.5), 2),
        "dissolved_oxygen": round(random.uniform(5.5, 8.0), 2),
        "salinity": round(random.uniform(29.0, 34.0), 2),
        "ph": round(random.uniform(7.6, 8.3), 2),
    }


def start_sensor_simulator(socketio, app):
    interval = app.config["SENSOR_INTERVAL_SECONDS"]
    with app.app_context():
        while True:
            reading = create_sensor_reading(generate_sensor_reading())
            socketio.emit("sensor_update", reading)

            sliders = CONTROL_STATE["sliders"]
            applyTemperatureControl(reading["temperature"], sliders["temperature_setpoint"])
            applyDissolvedOxygenControl(reading["dissolved_oxygen"], sliders["dissolved_oxygen_setpoint"])

            socketio.sleep(interval)
