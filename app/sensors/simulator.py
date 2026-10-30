import random
from datetime import datetime, timezone

from app.models import create_sensor_reading, get_latest_reading, get_control_settings
from app.api.piUtils import applyTemperatureControl, applyDissolvedOxygenControl, read_temp_sensor, read_secondary_temp_sensor, isRpiPresent
from app.api.routes import CONTROL_STATE

current_temp = None
current_do = None


def generate_sensor_reading():
    global current_temp, current_do
    
    # Initialize from DB if not already initialized in memory
    latest = get_latest_reading()
    if current_temp is None:
        current_temp = latest["temperature"] if latest else 26.0
    if current_do is None:
        current_do = latest["dissolved_oxygen"] if latest else 7.2
        
    # Read target settings from database
    targets = get_control_settings()
    target_temp = targets.get("temperature_setpoint", 26.0)
    target_do = targets.get("dissolved_oxygen_setpoint", 7.2)
    
    # Use real DS18B20 sensor on RPi; fall back to simulation otherwise
    sensor_temp = read_temp_sensor()
    if sensor_temp is not None:
        current_temp = sensor_temp
    else:
        # Gradually move current values toward target setpoints
        # Temperature: move 0.15 degrees toward target per step (3s)
        temp_diff = target_temp - current_temp
        if abs(temp_diff) > 0.01:
            step = 0.15 if temp_diff > 0 else -0.15
            if abs(temp_diff) < 0.15:
                current_temp = target_temp
            else:
                current_temp += step
        # Add a tiny bit of random noise (e.g. +-0.05)
        current_temp += random.uniform(-0.05, 0.05)
        current_temp = round(current_temp, 2)
    
    # Dissolved Oxygen: use ADS1115 AIN2 if available on RPi; fall back to simulation otherwise
    sensor_do = None
    #if isRpiPresent:
    #    try:
    #        from app.services.do_sensor import DOSensorService
    #        do_sensor = DOSensorService()
    #        sensor_do = do_sensor.read_do()
    #    except Exception:
    #        pass

    if sensor_do is not None:
        current_do = sensor_do
    else:
        print(f"[DUMMY] Dissolved Oxygen sensor failed to initialize - using simulated DO readings")
        # Move 0.1 mg/L toward target per step (3s)
        do_diff = target_do - current_do
        if abs(do_diff) > 0.01:
            step = 0.1 if do_diff > 0 else -0.1
            if abs(do_diff) < 0.1:
                current_do = target_do
            else:
                current_do += step
        # Add a tiny bit of random noise (e.g. +-0.03)
        current_do += random.uniform(-0.03, 0.03)
        current_do = round(current_do, 2)
    
    # Salinity: use ADS1115 AIN1 if available on RPi; fall back to simulation otherwise
    salinity = None
    # if isRpiPresent:
    #     try:
    #         from app.services.salinity_sensor import SalinitySensorService
    #         salinity_sensor = SalinitySensorService()
    #         salinity = salinity_sensor.read_salinity()
    #     except Exception:
    #         pass

    if salinity is None:
        print(f"[DUMMY] Salinity sensor failed to initialize - using simulated salinity readings")
        salinity = round(random.uniform(29.0, 34.0), 2)
    
    ph = None
    # if isRpiPresent:
    #     try:
    #         from app.services.ph_sensor import PHSensorService
    #         ph_sensor = PHSensorService()
    #         ph = ph_sensor.read_ph()
    #     except Exception:
    #         pass
            
    if ph is None:
        print(f"[DUMMY] pH sensor failed to initialize - using simulated pH readings")
        ph = round(random.uniform(7.6, 8.3), 2)
    
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "temperature": current_temp,
        "dissolved_oxygen": current_do,
        "salinity": salinity,
        "ph": ph,
    }


def start_sensor_simulator(socketio, app):
    interval = app.config["SENSOR_INTERVAL_SECONDS"]
    with app.app_context():
        while True:
            reading = create_sensor_reading(generate_sensor_reading())
            socketio.emit("sensor_update", reading)

            # Keep CONTROL_STATE sliders updated for any other parts of the app
            targets = get_control_settings()
            for key, val in targets.items():
                CONTROL_STATE["sliders"][key] = val
                
            secondary_temp = read_secondary_temp_sensor()
            applyTemperatureControl(reading["temperature"], targets.get("temperature_setpoint", 26.0), secondary_temp=secondary_temp)
            applyDissolvedOxygenControl(reading["dissolved_oxygen"], targets.get("dissolved_oxygen_setpoint", 7.2))

            socketio.sleep(interval)
