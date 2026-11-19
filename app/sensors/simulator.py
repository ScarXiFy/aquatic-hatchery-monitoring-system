import random
from datetime import datetime, timezone

from app.models import (
    create_sensor_reading,
    get_latest_reading,
    get_control_settings,
    get_latest_calibration,
)
from app.api.piUtils import (
    applyTemperatureControl,
    applyDissolvedOxygenControl,
    read_temp_sensor,
    read_secondary_temp_sensor,
    isRpiPresent,
)
from app.api.routes import CONTROL_STATE

current_temp = None
current_do_voltage = None
current_ph_voltage = None
current_sal_voltage = None


def generate_sensor_reading():
    global current_temp, current_do_voltage, current_ph_voltage, current_sal_voltage

    # Read target settings from database
    targets = get_control_settings()
    try:
        target_temp = float(targets.get("temperature_setpoint", 26.0))
    except (TypeError, ValueError):
        target_temp = 26.0

    try:
        target_do = float(targets.get("dissolved_oxygen_setpoint", 7.2))
    except (TypeError, ValueError):
        target_do = 7.2

    # 1. Main Tank Temperature (Digital DS18B20, raw value in °C)
    sensor_temp = read_temp_sensor()
    if sensor_temp is not None:
        if abs(sensor_temp - 85.0) < 0.01:
            print("[WARN] Main tank sensor returned invalid 85.0C power-on reading - ignoring")
            raw_temp = current_temp if current_temp is not None else 26.0
        else:
            raw_temp = sensor_temp
            current_temp = sensor_temp
    else:
        if current_temp is None:
            latest = get_latest_reading()
            if latest and "raw_temperature" in latest and abs(latest["raw_temperature"] - 85.0) >= 0.01:
                current_temp = latest["raw_temperature"]
            elif latest and "temperature" in latest and abs(latest["temperature"] - 85.0) >= 0.01:
                current_temp = latest["temperature"]
            else:
                current_temp = 26.0

        # Move simulated temperature toward target_temp setpoint
        temp_diff = target_temp - current_temp
        if abs(temp_diff) > 0.01:
            step = 0.15 if temp_diff > 0 else -0.15
            if abs(temp_diff) < 0.15:
                current_temp = target_temp
            else:
                current_temp += step
        current_temp += random.uniform(-0.05, 0.05)
        raw_temp = round(current_temp, 2)

    # 2. Secondary/Mixing Tank Temperature (Digital DS18B20, raw value in °C)
    secondary_temp = read_secondary_temp_sensor()
    if secondary_temp is not None:
        if abs(secondary_temp - 85.0) < 0.01:
            raw_mixing_temp = 26.0
        else:
            raw_mixing_temp = secondary_temp
    else:
        raw_mixing_temp = round(26.0 + random.uniform(-0.08, 0.08), 2)

    # 3. Dissolved Oxygen (Analog ADS1115 Channel 2, raw value in V)
    raw_do = None
    if isRpiPresent:
        try:
            from app.services.do_sensor import DOSensorService
            do_sensor = DOSensorService()
            val = do_sensor.read_raw_voltage() if hasattr(do_sensor, "read_raw_voltage") else None
            if isinstance(val, (int, float)):
                raw_do = float(val)
            elif hasattr(do_sensor, "read_do"):
                do_val = do_sensor.read_do()
                if isinstance(do_val, (int, float)):
                    raw_do = float(do_val)
        except Exception:
            pass

    if raw_do is not None:
        current_do_voltage = raw_do
    else:
        # Compute target voltage for simulated DO target based on latest calibration
        try:
            do_cal = get_latest_calibration("do")
            m, b = float(do_cal["slope"]), float(do_cal["offset"])
            target_v = (target_do - b) / m if abs(m) > 1e-4 else 1.162
        except Exception:
            target_v = 1.162

        if current_do_voltage is None:
            latest = get_latest_reading()
            if latest and "raw_dissolved_oxygen" in latest and isinstance(latest["raw_dissolved_oxygen"], (int, float)):
                current_do_voltage = float(latest["raw_dissolved_oxygen"])
            else:
                current_do_voltage = target_v

        try:
            v_diff = float(target_v) - float(current_do_voltage)
            if abs(v_diff) > 0.002:
                step = 0.015 if v_diff > 0 else -0.015
                if abs(v_diff) < 0.015:
                    current_do_voltage = target_v
                else:
                    current_do_voltage += step
            current_do_voltage += random.uniform(-0.004, 0.004)
            raw_do = round(float(current_do_voltage), 4)
        except Exception:
            current_do_voltage = 1.162
            raw_do = 1.162

    # 4. Salinity / EC (Analog ADS1115 Channel 1, raw value in V)
    raw_sal = None
    if isRpiPresent:
        try:
            from app.services.salinity_sensor import SalinitySensorService
            sal_sensor = SalinitySensorService()
            val = sal_sensor.read_raw_voltage() if hasattr(sal_sensor, "read_raw_voltage") else None
            if isinstance(val, (int, float)):
                raw_sal = float(val)
            elif hasattr(sal_sensor, "read_salinity"):
                sal_val = sal_sensor.read_salinity()
                if isinstance(sal_val, (int, float)):
                    raw_sal = float(sal_val)
        except Exception:
            pass

    if raw_sal is not None:
        current_sal_voltage = raw_sal
    else:
        if current_sal_voltage is None:
            latest = get_latest_reading()
            if latest and "raw_salinity" in latest and isinstance(latest["raw_salinity"], (int, float)):
                current_sal_voltage = float(latest["raw_salinity"])
            else:
                current_sal_voltage = 0.852  # ~32.6 ppt under standard slope

        current_sal_voltage += random.uniform(-0.005, 0.005)
        # Keep simulated voltage in reasonable range (0.75 - 0.95V)
        current_sal_voltage = max(0.75, min(0.95, current_sal_voltage))
        raw_sal = round(current_sal_voltage, 4)

    # 5. pH (Analog ADS1115 Channel 0, raw value in V)
    raw_ph = None
    if isRpiPresent:
        try:
            from app.services.ph_sensor import PHSensorService
            ph_sensor = PHSensorService()
            val = ph_sensor.read_raw_voltage() if hasattr(ph_sensor, "read_raw_voltage") else None
            if isinstance(val, (int, float)):
                raw_ph = float(val)
            elif hasattr(ph_sensor, "read_ph"):
                ph_val = ph_sensor.read_ph()
                if isinstance(ph_val, (int, float)):
                    raw_ph = float(ph_val)
        except Exception:
            pass

    if raw_ph is not None:
        current_ph_voltage = raw_ph
    else:
        if current_ph_voltage is None:
            latest = get_latest_reading()
            if latest and "raw_ph" in latest:
                current_ph_voltage = latest["raw_ph"]
            else:
                current_ph_voltage = 1.605  # ~7.00 pH under default calibration

        current_ph_voltage += random.uniform(-0.004, 0.004)
        current_ph_voltage = max(1.45, min(1.65, current_ph_voltage))
        raw_ph = round(current_ph_voltage, 4)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Raw sensor readings:
        "temperature": raw_temp,
        "dissolved_oxygen": raw_do,
        "salinity": raw_sal,
        "ph": raw_ph,
        "mixing_temperature": raw_mixing_temp,
        # Explicit raw keys:
        "raw_temperature": raw_temp,
        "raw_dissolved_oxygen": raw_do,
        "raw_salinity": raw_sal,
        "raw_ph": raw_ph,
        "raw_mixing_temperature": raw_mixing_temp,
    }


def start_sensor_simulator(socketio, app):
    interval = app.config["SENSOR_INTERVAL_SECONDS"]
    with app.app_context():
        while True:
            # create_sensor_reading stores raw values into raw_sensor_readings
            # and applies the latest calibration equations to return calibrated values
            reading = create_sensor_reading(generate_sensor_reading())
            socketio.emit("sensor_update", reading)

            # Keep CONTROL_STATE sliders updated for any other parts of the app
            targets = get_control_settings()
            for key, val in targets.items():
                CONTROL_STATE["sliders"][key] = val

            secondary_temp = reading.get("mixing_temperature")
            if secondary_temp is None:
                secondary_temp = read_secondary_temp_sensor()
            # Controls operate on calibrated values:
            applyTemperatureControl(
                reading["temperature"],
                targets.get("temperature_setpoint", 26.0),
                secondary_temp=secondary_temp,
            )
            applyDissolvedOxygenControl(
                reading["dissolved_oxygen"],
                targets.get("dissolved_oxygen_setpoint", 7.2),
            )

            socketio.sleep(interval)
