import time
from flask import Blueprint, jsonify, request, current_app
from app.api.piUtils import sourceValveState, drainValveState, mainValve, set_lux, setLedIntensity
import app.api.piUtils as piUtils

from app.models import (
    get_history,
    get_yesterday_or_last_day,
    get_latest_reading,
    get_thresholds,
    update_thresholds,
    get_control_settings,
    update_control_settings,
    get_notifications,
    create_notification,
    mark_notifications_read,
    clear_all_notifications,
    get_all_latest_calibrations,
    get_latest_calibration,
    add_sensor_calibration,
    get_calibration_history,
    SENSOR_NORMALIZATION,
    DEFAULT_CALIBRATIONS,
)

api_bp = Blueprint("api", __name__)

SERVER_BOOT_TIME_MS = int(time.time() * 1000)
SETPOINT_UPDATED_AT = {
    "temperature": SERVER_BOOT_TIME_MS,
    "dissolved_oxygen": SERVER_BOOT_TIME_MS,
}
IS_SYSTEM_START = {
    "temperature": True,
    "dissolved_oxygen": True,
}
SETPOINT_ADJUSTING = {
    "temperature": True,
    "dissolved_oxygen": True,
}


def update_adjusting_state_for_reading(reading, targets=None):
    """
    Updates server-side SETPOINT_ADJUSTING flags once a sensor reading reaches
    within the accepted optimal band of its setpoint (< 1.0°C for temperature,
    < 0.5 mg/L for dissolved oxygen).
    """
    if not reading:
        return
    if targets is None:
        targets = get_control_settings()
    for metric, slider_key, warning_limit in (
        ("temperature", "temperature_setpoint", 1.0),
        ("dissolved_oxygen", "dissolved_oxygen_setpoint", 0.5),
    ):
        if not SETPOINT_ADJUSTING.get(metric):
            continue
        val = reading.get(metric)
        target = targets.get(slider_key)
        if val is None or target is None:
            continue
        try:
            if abs(float(val) - float(target)) < warning_limit:
                SETPOINT_ADJUSTING[metric] = False
                IS_SYSTEM_START[metric] = False
        except (TypeError, ValueError):
            pass

CONTROL_STATE = {
    "valves": {
        "source": False,
        "drain": False,
        "main": False,
    },
    "sliders": {
        "temperature_setpoint": 26,
        "heating_tank_setpoint": 40,
        "dissolved_oxygen_setpoint": 7.2,
        "led_intensity": 1000,
    },
}


@api_bp.get("/readings/latest")
def latest_reading():
    return jsonify({"reading": get_latest_reading()})


@api_bp.get("/readings/trend")
def readings_trend():
    """
    Returns readings from yesterday, or the most recent previous day
    that has data, along with the date those readings are from.
    """
    readings, date = get_yesterday_or_last_day()
    return jsonify({
        "readings": readings,
        "date": date.isoformat() if date else None,
    })


@api_bp.get("/readings/history")
def readings_history():
    range_name = request.args.get("range", "day")
    if range_name not in {"day", "week"}:
        return jsonify({"error": "range must be day or week"}), 400
    mode = current_app.config.get("TRENDS_MODE", "last_24h")
    return jsonify({
        "range": range_name,
        "mode": mode,
        "readings": get_history(range_name, mode=mode)
    })


@api_bp.get("/thresholds")
def thresholds():
    return jsonify({"thresholds": get_thresholds()})


@api_bp.put("/thresholds")
def save_thresholds():
    payload = request.get_json(silent=True) or {}
    allowed_metrics = {"ph", "salinity"}
    updates = {}

    for metric in allowed_metrics:
        if metric not in payload:
            continue

        limits = payload[metric] or {}
        try:
            min_value = float(limits["min_value"])
            max_value = float(limits["max_value"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": f"{metric} thresholds require numeric min_value and max_value"}), 400

        if min_value >= max_value:
            return jsonify({"error": f"{metric} min_value must be less than max_value"}), 400

        updates[metric] = {"min_value": min_value, "max_value": max_value}

    unknown_metrics = set(payload) - allowed_metrics
    if unknown_metrics:
        return jsonify({"error": "only ph and salinity thresholds can be updated"}), 400

    if not updates:
        return jsonify({"error": "no threshold updates provided"}), 400

    res = update_thresholds(updates)
    try:
        from app import socketio
        if socketio:
            socketio.emit("thresholds_updated", {"thresholds": res})
    except Exception:
        pass

    return jsonify({"thresholds": res})


@api_bp.post("/controls/valves/<string:name>")
def set_valve(name):
    if name not in CONTROL_STATE["valves"]:
        return jsonify({"error": "unknown valve"}), 404

    payload = request.get_json(silent=True) or {}
    is_open = bool(payload.get("open", False))
    
    if name == "source":
        sourceValveState(is_open)
    elif name == "drain":
        drainValveState(is_open)
    elif name == "main":
        mainValve(is_open)
    else:
        return ("Unknown valve", 400)
    CONTROL_STATE["valves"][name] = is_open
    try:
        from app import socketio
        if socketio:
            socketio.emit("controls_updated", {
                "valves": CONTROL_STATE["valves"],
                "sliders": CONTROL_STATE["sliders"]
            })
    except Exception:
        pass
    return ("", 204)


HEATING_TANK_MIN_SETPOINT = 27.0
HEATING_TANK_MAX_SETPOINT = 40.0


def _min_valid_heating_tank_setpoint(main_temp_setpoint: float) -> float:
    return min(HEATING_TANK_MAX_SETPOINT, max(HEATING_TANK_MIN_SETPOINT, float(main_temp_setpoint) + 1.0))


@api_bp.get("/controls")
def get_controls():
    db_sliders = get_control_settings()
    defaults = {
        "temperature_setpoint": 26.0,
        "heating_tank_setpoint": 40.0,
        "dissolved_oxygen_setpoint": 7.2,
        "led_intensity": 1000.0,
    }
    updated = False
    for key, default in defaults.items():
        if key not in db_sliders:
            db_sliders[key] = default
            updated = True

    min_ht = _min_valid_heating_tank_setpoint(db_sliders["temperature_setpoint"])
    cur_ht = float(db_sliders["heating_tank_setpoint"])
    clamped_ht = max(min_ht, min(HEATING_TANK_MAX_SETPOINT, cur_ht))
    if clamped_ht != cur_ht:
        db_sliders["heating_tank_setpoint"] = clamped_ht
        updated = True

    if updated:
        db_sliders = update_control_settings(db_sliders)
        
    return jsonify({
        "valves": CONTROL_STATE["valves"],
        "sliders": db_sliders,
        "setpoint_times": SETPOINT_UPDATED_AT,
        "is_system_start": IS_SYSTEM_START,
        "adjusting_state": SETPOINT_ADJUSTING,
        "server_time": int(time.time() * 1000),
    })


@api_bp.post("/controls/sliders")
def set_sliders():
    payload = request.get_json(silent=True) or {}
    updates = {}
    now_ms = int(time.time() * 1000)
    prev_sliders = get_control_settings()
    for key in ["temperature_setpoint", "heating_tank_setpoint", "dissolved_oxygen_setpoint", "led_intensity"]:
        if key in payload:
            updates[key] = float(payload[key])

    effective_main_temp = float(
        updates.get("temperature_setpoint", prev_sliders.get("temperature_setpoint", 26.0))
    )

    if "heating_tank_setpoint" in updates:
        ht_val = updates["heating_tank_setpoint"]
        if ht_val < HEATING_TANK_MIN_SETPOINT or ht_val > HEATING_TANK_MAX_SETPOINT:
            return jsonify({
                "error": f"heating_tank_setpoint must be between {int(HEATING_TANK_MIN_SETPOINT)} and {int(HEATING_TANK_MAX_SETPOINT)}"
            }), 400
        if ht_val <= effective_main_temp:
            return jsonify({
                "error": "heating_tank_setpoint must be higher than temperature_setpoint"
            }), 400
    elif "temperature_setpoint" in updates:
        # If main tank setpoint was raised to/above the stored heating tank setpoint,
        # automatically raise heating_tank_setpoint so it remains strictly higher.
        prev_ht = float(prev_sliders.get("heating_tank_setpoint", HEATING_TANK_MAX_SETPOINT))
        min_ht = _min_valid_heating_tank_setpoint(effective_main_temp)
        if prev_ht < min_ht or prev_ht > HEATING_TANK_MAX_SETPOINT:
            updates["heating_tank_setpoint"] = max(min_ht, min(HEATING_TANK_MAX_SETPOINT, prev_ht))

    latest_reading = get_latest_reading()
    if "temperature_setpoint" in updates:
        prev_temp = prev_sliders.get("temperature_setpoint")
        if prev_temp is None or float(prev_temp) != updates["temperature_setpoint"]:
            SETPOINT_UPDATED_AT["temperature"] = now_ms
            IS_SYSTEM_START["temperature"] = False
            if latest_reading and latest_reading.get("temperature") is not None:
                SETPOINT_ADJUSTING["temperature"] = (
                    abs(float(latest_reading["temperature"]) - updates["temperature_setpoint"]) >= 1.0
                )
            else:
                SETPOINT_ADJUSTING["temperature"] = True
    if "dissolved_oxygen_setpoint" in updates:
        prev_do = prev_sliders.get("dissolved_oxygen_setpoint")
        if prev_do is None or float(prev_do) != updates["dissolved_oxygen_setpoint"]:
            SETPOINT_UPDATED_AT["dissolved_oxygen"] = now_ms
            IS_SYSTEM_START["dissolved_oxygen"] = False
            if latest_reading and latest_reading.get("dissolved_oxygen") is not None:
                SETPOINT_ADJUSTING["dissolved_oxygen"] = (
                    abs(float(latest_reading["dissolved_oxygen"]) - updates["dissolved_oxygen_setpoint"]) >= 0.5
                )
            else:
                SETPOINT_ADJUSTING["dissolved_oxygen"] = True
            
    if updates:
        db_sliders = update_control_settings(updates)
        if "led_intensity" in updates:
            piUtils.set_lux(updates["led_intensity"])
        if "heating_tank_setpoint" in updates:
            prev_ht = prev_sliders.get("heating_tank_setpoint")
            if (
                prev_ht is None
                or float(prev_ht) != updates["heating_tank_setpoint"]
                or set(updates.keys()) == {"heating_tank_setpoint"}
            ):
                piUtils.set_heating_tank_setpoint(updates["heating_tank_setpoint"])
    else:
        db_sliders = get_control_settings()
        
    for key, val in db_sliders.items():
        CONTROL_STATE["sliders"][key] = val

    try:
        from app import socketio
        if socketio:
            socketio.emit("controls_updated", {
                "sliders": db_sliders,
                "valves": CONTROL_STATE["valves"],
                "setpoint_times": SETPOINT_UPDATED_AT,
                "is_system_start": IS_SYSTEM_START,
                "adjusting_state": SETPOINT_ADJUSTING,
                "server_time": int(time.time() * 1000),
            })
    except Exception:
        pass
        
    return jsonify({
        "sliders": db_sliders,
        "setpoint_times": SETPOINT_UPDATED_AT,
        "is_system_start": IS_SYSTEM_START,
        "adjusting_state": SETPOINT_ADJUSTING,
        "server_time": int(time.time() * 1000),
    })


@api_bp.post("/controls/led")
def set_led():
    payload = request.get_json(silent=True) or {}
    if "lux" in payload:
        target_lux = float(payload["lux"])
    elif "led_intensity" in payload:
        target_lux = float(payload["led_intensity"])
    else:
        return jsonify({"error": "missing lux or led_intensity in payload"}), 400

    piUtils.set_lux(target_lux)
    update_control_settings({"led_intensity": target_lux})
    CONTROL_STATE["sliders"]["led_intensity"] = target_lux

    try:
        from app import socketio
        if socketio:
            socketio.emit("controls_updated", {
                "sliders": CONTROL_STATE["sliders"],
                "valves": CONTROL_STATE["valves"]
            })
    except Exception:
        pass

    return jsonify({"led_intensity": target_lux, "lux": target_lux}), 200


@api_bp.get("/notifications")
def fetch_notifications():
    notifications, unread_count = get_notifications()
    return jsonify({
        "notifications": notifications,
        "unread_count": unread_count
    })


@api_bp.post("/notifications")
def save_notification():
    payload = request.get_json(silent=True) or {}
    required = ["parameter", "status", "current_value", "threshold_value", "message"]
    if not all(k in payload for k in required):
        return jsonify({"error": "missing required notification fields"}), 400
        
    n = create_notification(payload)
    return jsonify({"notification": n}), 201


@api_bp.put("/notifications/read")
def mark_read():
    notifications, unread_count = mark_notifications_read()
    return jsonify({
        "notifications": notifications,
        "unread_count": unread_count
    })


@api_bp.delete("/notifications")
def delete_notifications():
    notifications, unread_count = clear_all_notifications()
    return jsonify({
        "notifications": notifications,
        "unread_count": unread_count
    })


# ---------------------------------------------------------------------------
# Calibration API Endpoints
# ---------------------------------------------------------------------------

@api_bp.get("/calibrations")
def get_calibrations():
    """
    Returns the most recent calibration parameters for all 5 sensors.
    """
    return jsonify({"calibrations": get_all_latest_calibrations()})


@api_bp.get("/calibrations/<string:sensor>")
def get_sensor_calibration(sensor):
    """
    Returns the most recent calibration and calibration history for the requested sensor.
    """
    try:
        latest = get_latest_calibration(sensor)
        history = get_calibration_history(sensor)
        return jsonify({
            "sensor": latest["sensor"],
            "calibration": latest,
            "history": history,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


@api_bp.post("/calibrations")
@api_bp.post("/calibrations/<string:sensor>")
def save_calibration(sensor=None):
    """
    Saves a new calibration equation (slope, offset, created_at) for a sensor.
    Adds a new record to the table without overwriting prior records.
    """
    payload = request.get_json(silent=True) or {}
    sensor_key = sensor or payload.get("sensor")
    if not sensor_key:
        return jsonify({"error": "sensor is required"}), 400

    if "slope" not in payload or "offset" not in payload:
        return jsonify({"error": "slope and offset are required"}), 400

    try:
        slope = float(payload["slope"])
        offset = float(payload["offset"])
        created_at = payload.get("commissionedAt") or payload.get("created_at")
        new_cal = add_sensor_calibration(sensor_key, slope, offset, created_at=created_at)
        return jsonify({
            "success": True,
            "calibration": new_cal,
            "message": f"Successfully commissioned calibration for {new_cal['sensor']}",
        }), 201
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400


@api_bp.post("/calibrations/<string:sensor>/reset")
def reset_calibration(sensor):
    """
    Resets calibration parameters for the sensor back to starting default values
    by recording a new default calibration entry.
    """
    norm_sensor = SENSOR_NORMALIZATION.get(sensor.lower())
    if not norm_sensor or norm_sensor not in DEFAULT_CALIBRATIONS:
        return jsonify({"error": f"Unknown sensor: {sensor}"}), 404

    defaults = DEFAULT_CALIBRATIONS[norm_sensor]
    cal = add_sensor_calibration(norm_sensor, defaults["slope"], defaults["offset"])
    return jsonify({
        "success": True,
        "message": f"Reset {norm_sensor} to starting default calibration",
        "calibration": cal,
    }), 200


# ---------------------------------------------------------------------------
# Maintenance Mode API Endpoints
# ---------------------------------------------------------------------------

def _emit_maintenance_update(state):
    try:
        from app import socketio
        if socketio:
            socketio.emit("maintenance_updated", state)
    except Exception:
        pass


@api_bp.get("/maintenance")
def get_maintenance():
    """Return current maintenance mode status and component states."""
    return jsonify(piUtils.get_maintenance_state()), 200


@api_bp.post("/maintenance/mode")
def set_maintenance_mode():
    """Enable or disable isolated maintenance mode (reverts states when disabling)."""
    payload = request.get_json(silent=True) or {}
    if "active" in payload:
        enable = bool(payload["active"])
    elif "enabled" in payload:
        enable = bool(payload["enabled"])
    else:
        return jsonify({"error": "missing active boolean in payload"}), 400

    state = piUtils.enter_maintenance_mode() if enable else piUtils.exit_maintenance_mode()
    _emit_maintenance_update(state)
    return jsonify(state), 200


@api_bp.post("/maintenance/component")
def set_maintenance_component_route():
    """Manually control a component while maintenance mode is active."""
    payload = request.get_json(silent=True) or {}
    component = payload.get("component")
    if not component or not isinstance(component, str):
        return jsonify({"error": "component is required"}), 400

    comp_name = component.strip()
    if comp_name == "bleed_valve":
        raw_val = None
        for key in ("state", "percent", "opening", "position", "value"):
            if key in payload:
                raw_val = payload[key]
                break
        if raw_val is None or isinstance(raw_val, bool):
            return jsonify({"error": "bleed_valve opening must be one of 0, 33, 66, 100"}), 400
        desired = raw_val
    else:
        if "state" in payload:
            desired = bool(payload["state"])
        elif "open" in payload:
            desired = bool(payload["open"])
        elif "active" in payload:
            desired = bool(payload["active"])
        else:
            return jsonify({"error": "state boolean is required"}), 400

    try:
        state = piUtils.set_maintenance_component(comp_name, desired)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _emit_maintenance_update(state)
    return jsonify(state), 200
