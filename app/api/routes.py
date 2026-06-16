from flask import Blueprint, jsonify, request

from app.models import get_history, get_latest_reading, get_thresholds

api_bp = Blueprint("api", __name__)

CONTROL_STATE = {
    "valves": {
        "source": False,
        "drain": False,
    },
    "sliders": {
        "water_flow": 55,
        "aeration": 70,
        "feeding": 40,
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


@api_bp.post("/controls/valves/<string:name>")
def set_valve(name):
    if name not in CONTROL_STATE["valves"]:
        return jsonify({"error": "unknown valve"}), 404

    payload = request.get_json(silent=True) or {}
    is_open = bool(payload.get("open", False))
    CONTROL_STATE["valves"][name] = is_open
    return jsonify({"name": name, "open": is_open})


@api_bp.post("/controls/sliders")
def set_sliders():
    payload = request.get_json(silent=True) or {}
    for key in CONTROL_STATE["sliders"]:
        if key in payload:
            CONTROL_STATE["sliders"][key] = int(payload[key])
    return jsonify({"sliders": CONTROL_STATE["sliders"]})
