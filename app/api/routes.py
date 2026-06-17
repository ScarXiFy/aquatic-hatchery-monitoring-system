from flask import Blueprint, jsonify, request
from app.api.piUtils import sourceValveState, drainValveState

from app.models import get_history, get_latest_reading, get_thresholds, update_thresholds

api_bp = Blueprint("api", __name__)

CONTROL_STATE = {
    "valves": {
        "source": True,
        "drain": False,
    },
    "sliders": {
        "temperature_setpoint": 26,
        "dissolved_oxygen_setpoint": 7.2,
        "led_intensity": 1000,
    },
}


@api_bp.get("/readings/latest")
def latest_reading():
    return jsonify({"reading": get_latest_reading()})


@api_bp.get("/readings/history")
def readings_history():
    range_name = request.args.get("range", "day")
    if range_name not in {"day", "week"}:
        return jsonify({"error": "range must be day or week"}), 400
    return jsonify({"range": range_name, "readings": get_history(range_name)})


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

    return jsonify({"thresholds": update_thresholds(updates)})


@api_bp.post("/controls/valves/<string:name>")
def set_valve(name):
    if name not in CONTROL_STATE["valves"]:
        return jsonify({"error": "unknown valve"}), 404

    payload = request.get_json(silent=True) or {}
    is_open = bool(payload.get("open", False))
    # CONTROL_STATE["valves"][name] = is_open
    # return jsonify({"name": name, "open": is_open})
    
    # isOpen = (state == 1)
    if name == "source":
        sourceValveState(is_open)
    elif name == "drain":
        drainValveState(is_open)
    else:
        return ("Unknown valve", 400)
    return ("", 204)


@api_bp.post("/controls/sliders")
def set_sliders():
    payload = request.get_json(silent=True) or {}
    for key in CONTROL_STATE["sliders"]:
        if key in payload:
            CONTROL_STATE["sliders"][key] = float(payload[key])
    return jsonify({"sliders": CONTROL_STATE["sliders"]})

@api_bp.post("/threshold/<string:metric>")
def setThreshold(metric):
    data = request.get_json()
    field = data["field"]
    value = float(data["value"])
    
    update_threshold_field(metric, field, value)

    return ("", 204)