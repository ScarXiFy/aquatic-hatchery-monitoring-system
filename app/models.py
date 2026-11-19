from datetime import datetime, timedelta, timezone

from app.database import get_db

DEFAULT_THRESHOLDS = {
    "temperature": (24.0, 30.0),
    "dissolved_oxygen": (5.0, 8.5),
    "salinity": (28.0, 35.0),
    "ph": (7.4, 8.4),
}

# Default starting calibration values for each of the 5 sensors
# Previously hardcoded in the codebase
DEFAULT_CALIBRATIONS = {
    "ph": {
        "slope": -6.41401,
        "offset": 17.29167,
    },
    "do": {
        "slope": 5.93086,
        "offset": 0.3273368,
    },
    "salinity": {
        "slope": 38.265306,
        "offset": 0.0,
    },
    "temp": {
        "slope": 1.0,
        "offset": 0.0,
    },
    "mixing_temp": {
        "slope": 1.0,
        "offset": 0.0,
    },
}

SENSOR_TABLE_MAP = {
    "ph": "ph_calibration",
    "do": "do_calibration",
    "salinity": "salinity_calibration",
    "temp": "temp_calibration",
    "mixing_temp": "mixing_temp_calibration",
}

SENSOR_NORMALIZATION = {
    "ph": "ph",
    "ph_sensor": "ph",
    "do": "do",
    "dissolved_oxygen": "do",
    "salinity": "salinity",
    "ec": "salinity",
    "temp": "temp",
    "temperature": "temp",
    "main_temp": "temp",
    "main_tank_temp": "temp",
    "mixing_temp": "mixing_temp",
    "temp_mixing": "mixing_temp",
    "mixing": "mixing_temp",
    "mixing_tank_temp": "mixing_temp",
    "mixing_temperature": "mixing_temp",
}

SENSOR_LIMITS = {
    "ph": (0.0, 14.0),
    "do": (0.0, 20.0),
    "salinity": (0.0, 50.0),
    "temp": (0.0, 60.0),
    "mixing_temp": (0.0, 60.0),
}


# ---------------------------------------------------------------------------
# Calibration Database Management
# ---------------------------------------------------------------------------

_calibrations_ensured = False

def ensure_default_calibrations(db=None):
    """
    Ensures that default starting calibration entries exist for each of the 5 sensors
    in both the unified sensor_calibrations table and the dedicated sensor tables.
    """
    global _calibrations_ensured
    if _calibrations_ensured and db is None:
        return

    if db is None:
        db = get_db()

    # Ensure tables exist just in case schema hasn't executed
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS sensor_calibrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor TEXT NOT NULL,
            slope REAL NOT NULL,
            offset REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    for tbl in SENSOR_TABLE_MAP.values():
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {tbl} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slope REAL NOT NULL,
                offset REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    for sensor, defaults in DEFAULT_CALIBRATIONS.items():
        row = db.execute(
            "SELECT 1 FROM sensor_calibrations WHERE sensor = ? LIMIT 1",
            (sensor,),
        ).fetchone()
        if not row:
            created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.execute(
                """
                INSERT INTO sensor_calibrations (sensor, slope, offset, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (sensor, defaults["slope"], defaults["offset"], created_at),
            )
            tbl = SENSOR_TABLE_MAP.get(sensor)
            if tbl:
                db.execute(
                    f"INSERT INTO {tbl} (slope, offset, created_at) VALUES (?, ?, ?)",
                    (defaults["slope"], defaults["offset"], created_at),
                )
    db.commit()
    _calibrations_ensured = True


def get_latest_calibration(sensor: str, db=None) -> dict:
    """
    Retrieves the most recent slope and offset values stored in the database
    for the requested sensor. Falls back to starting default values if none found.
    """
    norm_sensor = SENSOR_NORMALIZATION.get(sensor.lower(), sensor.lower())
    if db is None:
        db = get_db()

    try:
        row = db.execute(
            """
            SELECT sensor, slope, offset, created_at
            FROM sensor_calibrations
            WHERE sensor = ?
            ORDER BY id DESC, created_at DESC
            LIMIT 1
            """,
            (norm_sensor,),
        ).fetchone()
        if row:
            return {
                "sensor": row["sensor"],
                "slope": float(row["slope"]),
                "offset": float(row["offset"]),
                "created_at": row["created_at"],
            }
    except Exception:
        pass

    defaults = DEFAULT_CALIBRATIONS.get(norm_sensor, {"slope": 1.0, "offset": 0.0})
    return {
        "sensor": norm_sensor,
        "slope": defaults["slope"],
        "offset": defaults["offset"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def get_all_latest_calibrations(db=None) -> dict:
    """
    Retrieves the most recent calibration info for all 5 sensors.
    """
    if db is None:
        db = get_db()
    ensure_default_calibrations(db)
    result = {}
    for sensor in DEFAULT_CALIBRATIONS:
        result[sensor] = get_latest_calibration(sensor, db=db)
    return result


def add_sensor_calibration(sensor: str, slope: float, offset: float, created_at: str = None, db=None) -> dict:
    """
    Applies a new calibration equation by adding a new record to the table
    (does NOT overwrite previous calibration equations).
    Also records to the dedicated sensor table.
    """
    norm_sensor = SENSOR_NORMALIZATION.get(sensor.lower())
    if not norm_sensor:
        raise ValueError(f"Unknown sensor: {sensor}")
    if db is None:
        db = get_db()
    ensure_default_calibrations(db)

    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    slope_val = float(slope)
    offset_val = float(offset)

    cursor = db.execute(
        """
        INSERT INTO sensor_calibrations (sensor, slope, offset, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (norm_sensor, slope_val, offset_val, created_at),
    )
    tbl = SENSOR_TABLE_MAP.get(norm_sensor)
    if tbl:
        db.execute(
            f"INSERT INTO {tbl} (slope, offset, created_at) VALUES (?, ?, ?)",
            (slope_val, offset_val, created_at),
        )
    db.commit()

    return {
        "id": cursor.lastrowid,
        "sensor": norm_sensor,
        "slope": slope_val,
        "offset": offset_val,
        "created_at": created_at,
    }


def get_calibration_history(sensor: str, limit: int = 50, db=None) -> list:
    """
    Returns history of calibrations for a given sensor, ordered most recent first.
    """
    norm_sensor = SENSOR_NORMALIZATION.get(sensor.lower())
    if not norm_sensor:
        raise ValueError(f"Unknown sensor: {sensor}")
    if db is None:
        db = get_db()
    rows = db.execute(
        """
        SELECT id, sensor, slope, offset, created_at
        FROM sensor_calibrations
        WHERE sensor = ?
        ORDER BY id DESC, created_at DESC
        LIMIT ?
        """,
        (norm_sensor, int(limit)),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "sensor": r["sensor"],
            "slope": r["slope"],
            "offset": r["offset"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def apply_calibration(sensor: str, raw_value: float, calibrations: dict = None, temp_c: float = None) -> float:
    """
    Applies the linear calibration equation (y = slope * raw_value + offset)
    using the most recent stored calibration parameters, clamped to sensor limits.
    """
    norm_sensor = SENSOR_NORMALIZATION.get(sensor.lower(), sensor.lower())
    if raw_value is None:
        return None
    try:
        raw_val = float(raw_value)
    except (ValueError, TypeError):
        return None

    if calibrations and norm_sensor in calibrations:
        slope = calibrations[norm_sensor]["slope"]
        offset = calibrations[norm_sensor]["offset"]
    else:
        cal = get_latest_calibration(norm_sensor)
        slope = cal["slope"]
        offset = cal["offset"]

    # Linear calibration equation
    val = slope * raw_val + offset

    # Clamp within physical limits
    limits = SENSOR_LIMITS.get(norm_sensor)
    if limits:
        val = max(limits[0], min(limits[1], val))

    return round(val, 2)


# ---------------------------------------------------------------------------
# Sensor Readings & History
# ---------------------------------------------------------------------------

def row_to_reading(row, calibrations=None):
    """
    Converts a database row from raw_sensor_readings into a calibrated reading dictionary
    for display on the frontend, applying the most recent stored calibration equations.
    Also exposes the underlying raw readings.
    """
    if calibrations is None:
        calibrations = get_all_latest_calibrations()

    raw_temp = float(row["temperature"])
    raw_do = float(row["dissolved_oxygen"])
    raw_sal = float(row["salinity"])
    raw_ph = float(row["ph"])
    raw_mix = (
        float(row["mixing_temperature"])
        if ("mixing_temperature" in row.keys() and row["mixing_temperature"] is not None)
        else 26.0
    )

    # Calibrate values using the most recent equations in the database
    cal_temp = apply_calibration("temp", raw_temp, calibrations)
    cal_do = apply_calibration("do", raw_do, calibrations)
    cal_sal = apply_calibration("salinity", raw_sal, calibrations, temp_c=cal_temp)
    cal_ph = apply_calibration("ph", raw_ph, calibrations)
    cal_mix = apply_calibration("mixing_temp", raw_mix, calibrations)

    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        # Calibrated values for dashboard, charts, alerts, and controls
        "temperature": cal_temp,
        "dissolved_oxygen": cal_do,
        "salinity": cal_sal,
        "ph": cal_ph,
        "mixing_temperature": cal_mix,
        # Raw sensor readings stored in DB (voltages for analog, temp in °C for digital)
        "raw_temperature": raw_temp,
        "raw_dissolved_oxygen": raw_do,
        "raw_salinity": raw_sal,
        "raw_ph": raw_ph,
        "raw_mixing_temperature": raw_mix,
        "raw": {
            "temperature": raw_temp,
            "dissolved_oxygen": raw_do,
            "salinity": raw_sal,
            "ph": raw_ph,
            "mixing_temperature": raw_mix,
        },
    }


def ensure_default_thresholds():
    db = get_db()
    for metric, limits in DEFAULT_THRESHOLDS.items():
        db.execute(
            """
            INSERT OR IGNORE INTO thresholds (metric, min_value, max_value)
            VALUES (?, ?, ?)
            """,
            (metric, limits[0], limits[1]),
        )
    db.commit()


def create_sensor_reading(reading):
    """
    Stores raw sensor readings (voltage for analog, temperature for digital)
    into the new reference table raw_sensor_readings.
    Dynamically applies the latest calibration equations stored in the database
    and returns calibrated values for display to the frontend.
    """
    db = get_db()
    ensure_default_calibrations(db)

    # Extract raw readings (supports both 'raw_ph' and 'ph' keys)
    raw_temp = float(reading.get("raw_temperature", reading.get("temperature", 26.0)))
    raw_do = float(reading.get("raw_dissolved_oxygen", reading.get("dissolved_oxygen", 1.162)))
    raw_sal = float(reading.get("raw_salinity", reading.get("salinity", 0.852)))
    raw_ph = float(reading.get("raw_ph", reading.get("ph", 1.605)))
    raw_mix = reading.get("raw_mixing_temperature", reading.get("mixing_temperature", 26.0))
    if raw_mix is not None:
        raw_mix = float(raw_mix)
    timestamp = reading.get("timestamp") or datetime.now(timezone.utc).isoformat(timespec="seconds")

    cursor = db.execute(
        """
        INSERT INTO raw_sensor_readings (
            timestamp,
            temperature,
            dissolved_oxygen,
            salinity,
            ph,
            mixing_temperature
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (timestamp, raw_temp, raw_do, raw_sal, raw_ph, raw_mix),
    )
    db.commit()
    row_id = cursor.lastrowid

    # Retrieve most recent calibrations
    calibrations = get_all_latest_calibrations(db)

    # Calculate calibrated values using DB equations
    cal_temp = apply_calibration("temp", raw_temp, calibrations)
    cal_do = apply_calibration("do", raw_do, calibrations)
    cal_sal = apply_calibration("salinity", raw_sal, calibrations, temp_c=cal_temp)
    cal_ph = apply_calibration("ph", raw_ph, calibrations)
    cal_mix = apply_calibration("mixing_temp", raw_mix if raw_mix is not None else 26.0, calibrations)

    return {
        "id": row_id,
        "timestamp": timestamp,
        "temperature": cal_temp,
        "dissolved_oxygen": cal_do,
        "salinity": cal_sal,
        "ph": cal_ph,
        "mixing_temperature": cal_mix,
        "raw_temperature": raw_temp,
        "raw_dissolved_oxygen": raw_do,
        "raw_salinity": raw_sal,
        "raw_ph": raw_ph,
        "raw_mixing_temperature": raw_mix,
        "raw": {
            "temperature": raw_temp,
            "dissolved_oxygen": raw_do,
            "salinity": raw_sal,
            "ph": raw_ph,
            "mixing_temperature": raw_mix,
        },
    }


def get_latest_reading():
    """
    Returns the latest sensor reading, dynamically calibrated from the reference table raw_sensor_readings.
    Falls back to legacy sensor_readings if raw table has no records.
    """
    db = get_db()
    ensure_default_calibrations(db)

    try:
        row = db.execute(
            """
            SELECT id, timestamp, temperature, dissolved_oxygen, salinity, ph, mixing_temperature
            FROM raw_sensor_readings
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if row:
            calibrations = get_all_latest_calibrations(db)
            return row_to_reading(row, calibrations)
    except Exception:
        pass

    # Fallback to legacy sensor_readings if present
    try:
        legacy_row = db.execute(
            """
            SELECT id, timestamp, temperature, dissolved_oxygen, salinity, ph
            FROM sensor_readings
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if legacy_row:
            return {
                "id": legacy_row["id"],
                "timestamp": legacy_row["timestamp"],
                "temperature": legacy_row["temperature"],
                "dissolved_oxygen": legacy_row["dissolved_oxygen"],
                "salinity": legacy_row["salinity"],
                "ph": legacy_row["ph"],
            }
    except Exception:
        pass

    return None


def get_history(range_name="day", mode="last_24h"):
    """
    Returns historical readings from raw_sensor_readings with calibrations dynamically applied.
    """
    if range_name == "week":
        until = datetime.now(timezone.utc)
        since = until - timedelta(hours=168)
        start_str = since.isoformat(timespec="seconds")
        end_str = until.isoformat(timespec="seconds")
    else:  # "day"
        if mode == "yesterday":
            local_now = datetime.now().astimezone()
            yesterday_date = (local_now - timedelta(days=1)).date()
            start_local = datetime.combine(yesterday_date, datetime.min.time()).replace(tzinfo=local_now.tzinfo)
            end_local = datetime.combine(yesterday_date, datetime.max.time()).replace(tzinfo=local_now.tzinfo)
            start_utc = start_local.astimezone(timezone.utc)
            end_utc = end_local.astimezone(timezone.utc)
            start_str = start_utc.isoformat(timespec="seconds")
            end_str = end_utc.isoformat(timespec="seconds")
        else:  # "last_24h"
            until = datetime.now(timezone.utc)
            since = until - timedelta(hours=24)
            start_str = since.isoformat(timespec="seconds")
            end_str = until.isoformat(timespec="seconds")

    db = get_db()
    ensure_default_calibrations(db)

    try:
        rows = db.execute(
            """
            SELECT id, timestamp, temperature, dissolved_oxygen, salinity, ph, mixing_temperature
            FROM raw_sensor_readings
            WHERE timestamp >= ?
              AND timestamp <= ?
              AND temperature IS NOT NULL AND temperature != ''
              AND dissolved_oxygen IS NOT NULL AND dissolved_oxygen != ''
              AND salinity IS NOT NULL AND salinity != ''
              AND ph IS NOT NULL AND ph != ''
            ORDER BY timestamp ASC, id ASC
            """,
            (start_str, end_str),
        ).fetchall()
        if rows:
            calibrations = get_all_latest_calibrations(db)
            return [row_to_reading(row, calibrations) for row in rows]
    except Exception:
        pass

    # Legacy fallback
    try:
        legacy_rows = db.execute(
            """
            SELECT id, timestamp, temperature, dissolved_oxygen, salinity, ph
            FROM sensor_readings
            WHERE timestamp >= ?
              AND timestamp <= ?
              AND temperature IS NOT NULL AND temperature != ''
              AND dissolved_oxygen IS NOT NULL AND dissolved_oxygen != ''
              AND salinity IS NOT NULL AND salinity != ''
              AND ph IS NOT NULL AND ph != ''
            ORDER BY timestamp ASC, id ASC
            """,
            (start_str, end_str),
        ).fetchall()
        if legacy_rows:
            return [
                {
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "temperature": r["temperature"],
                    "dissolved_oxygen": r["dissolved_oxygen"],
                    "salinity": r["salinity"],
                    "ph": r["ph"],
                }
                for r in legacy_rows
            ]
    except Exception:
        pass

    return []


def get_yesterday_or_last_day():
    """
    Return all readings from yesterday (local time) using raw_sensor_readings as reference table.
    """
    local_now = datetime.now().astimezone()
    db = get_db()
    ensure_default_calibrations(db)

    today_start_local = datetime.combine(local_now.date(), datetime.min.time()).replace(tzinfo=local_now.tzinfo)
    today_start_utc = today_start_local.astimezone(timezone.utc).isoformat(timespec="seconds")

    cutoff_local = datetime.combine(local_now.date() - timedelta(days=30), datetime.min.time()).replace(tzinfo=local_now.tzinfo)
    cutoff_utc = cutoff_local.astimezone(timezone.utc).isoformat(timespec="seconds")

    latest_prior_row = None
    try:
        latest_prior_row = db.execute(
            """
            SELECT timestamp FROM raw_sensor_readings
            WHERE timestamp >= ? AND timestamp < ?
              AND temperature IS NOT NULL AND temperature != ''
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (cutoff_utc, today_start_utc),
        ).fetchone()
    except Exception:
        pass

    if not latest_prior_row:
        # Check legacy table
        try:
            latest_prior_row = db.execute(
                """
                SELECT timestamp FROM sensor_readings
                WHERE timestamp >= ? AND timestamp < ?
                  AND temperature IS NOT NULL AND temperature != ''
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (cutoff_utc, today_start_utc),
            ).fetchone()
        except Exception:
            pass

    if not latest_prior_row:
        return [], None

    ts_str = latest_prior_row["timestamp"]
    try:
        reading_dt = datetime.fromisoformat(ts_str)
        if reading_dt.tzinfo is None:
            reading_dt = reading_dt.replace(tzinfo=timezone.utc)
        target_date = reading_dt.astimezone(local_now.tzinfo).date()
    except Exception:
        return [], None

    start_local = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=local_now.tzinfo)
    end_local   = datetime.combine(target_date, datetime.max.time()).replace(tzinfo=local_now.tzinfo)
    start_str = start_local.astimezone(timezone.utc).isoformat(timespec="seconds")
    end_str   = end_local.astimezone(timezone.utc).isoformat(timespec="seconds")

    try:
        rows = db.execute(
            """
            SELECT id, timestamp, temperature, dissolved_oxygen, salinity, ph, mixing_temperature
            FROM raw_sensor_readings
            WHERE timestamp >= ?
              AND timestamp <= ?
              AND temperature IS NOT NULL AND temperature != ''
              AND dissolved_oxygen IS NOT NULL AND dissolved_oxygen != ''
              AND salinity IS NOT NULL AND salinity != ''
              AND ph IS NOT NULL AND ph != ''
            ORDER BY timestamp ASC, id ASC
            """,
            (start_str, end_str),
        ).fetchall()
        if rows:
            calibrations = get_all_latest_calibrations(db)
            return [row_to_reading(row, calibrations) for row in rows], target_date
    except Exception:
        pass

    try:
        legacy_rows = db.execute(
            """
            SELECT id, timestamp, temperature, dissolved_oxygen, salinity, ph
            FROM sensor_readings
            WHERE timestamp >= ?
              AND timestamp <= ?
              AND temperature IS NOT NULL AND temperature != ''
              AND dissolved_oxygen IS NOT NULL AND dissolved_oxygen != ''
              AND salinity IS NOT NULL AND salinity != ''
              AND ph IS NOT NULL AND ph != ''
            ORDER BY timestamp ASC, id ASC
            """,
            (start_str, end_str),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "temperature": r["temperature"],
                "dissolved_oxygen": r["dissolved_oxygen"],
                "salinity": r["salinity"],
                "ph": r["ph"],
            }
            for r in legacy_rows
        ], target_date
    except Exception:
        pass

    return [], None


# ---------------------------------------------------------------------------
# Control Settings & Thresholds
# ---------------------------------------------------------------------------

def get_control_settings():
    db = get_db()
    rows = db.execute(
        """
        SELECT metric, min_value FROM thresholds 
        WHERE metric IN ('temperature', 'dissolved_oxygen', 'led_intensity')
        """
    ).fetchall()
    res = {}
    for row in rows:
        if row["metric"] == "temperature":
            res["temperature_setpoint"] = row["min_value"]
        elif row["metric"] == "dissolved_oxygen":
            res["dissolved_oxygen_setpoint"] = row["min_value"]
        elif row["metric"] == "led_intensity":
            res["led_intensity"] = row["min_value"]
    return res


def update_control_settings(updates):
    db = get_db()
    for metric, value in updates.items():
        if metric == "temperature_setpoint":
            db.execute(
                "UPDATE thresholds SET min_value = ?, max_value = ? WHERE metric = 'temperature'",
                (float(value), float(value)),
            )
        elif metric == "dissolved_oxygen_setpoint":
            db.execute(
                "UPDATE thresholds SET min_value = ?, max_value = ? WHERE metric = 'dissolved_oxygen'",
                (float(value), float(value)),
            )
        elif metric == "led_intensity":
            db.execute(
                """
                INSERT INTO thresholds (metric, min_value, max_value)
                VALUES ('led_intensity', ?, ?)
                ON CONFLICT(metric) DO UPDATE SET min_value = excluded.min_value, max_value = excluded.max_value
                """,
                (float(value), float(value)),
            )
    db.commit()
    return get_control_settings()


def init_system_controls():
    db = get_db()
    ensure_default_thresholds()
    ensure_default_calibrations(db)

    row = db.execute("SELECT 1 FROM thresholds WHERE metric = 'led_intensity'").fetchone()
    if row:
        return

    latest = get_latest_reading()
    if latest:
        temp_init = latest["temperature"]
        if abs(float(temp_init) - 85.0) < 0.01:
            temp_init = 26.0
        do_init = latest["dissolved_oxygen"]
        db.execute(
            "UPDATE thresholds SET min_value = ?, max_value = ? WHERE metric = 'temperature'",
            (float(temp_init), float(temp_init)),
        )
        db.execute(
            "UPDATE thresholds SET min_value = ?, max_value = ? WHERE metric = 'dissolved_oxygen'",
            (float(do_init), float(do_init)),
        )

    db.execute(
        "INSERT INTO thresholds (metric, min_value, max_value) VALUES ('led_intensity', 1000.0, 1000.0)"
    )
    db.commit()


def get_thresholds():
    ensure_default_thresholds()
    rows = get_db().execute(
        """
        SELECT metric, min_value, max_value
        FROM thresholds
        ORDER BY metric ASC
        """
    ).fetchall()
    return [
        {
            "metric": row["metric"],
            "min_value": row["min_value"],
            "max_value": row["max_value"],
        }
        for row in rows
    ]


def update_thresholds(updates):
    ensure_default_thresholds()
    allowed_metrics = {"ph", "salinity"}
    db = get_db()

    for metric, limits in updates.items():
        if metric not in allowed_metrics:
            raise ValueError("only ph and salinity thresholds can be updated")

        min_value = float(limits["min_value"])
        max_value = float(limits["max_value"])
        if min_value >= max_value:
            raise ValueError("minimum threshold must be less than maximum threshold")

        db.execute(
            """
            UPDATE thresholds
            SET min_value = ?, max_value = ?
            WHERE metric = ?
            """,
            (min_value, max_value, metric),
        )

    db.commit()
    return get_thresholds()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def cleanup_old_notifications(db=None):
    try:
        local_now = datetime.now().astimezone()
        start_of_local_today = datetime.combine(local_now.date(), datetime.min.time()).replace(tzinfo=local_now.tzinfo)
        start_utc_str = start_of_local_today.astimezone(timezone.utc).isoformat(timespec="seconds")

        if db is None:
            db = get_db()
        db.execute(
            "DELETE FROM notifications WHERE datetime(created_at) < datetime(?)",
            (start_utc_str,),
        )
        db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to cleanup old notifications: {e}")


def create_notification(data, db=None):
    cleanup_old_notifications(db=db)
    if db is None:
        db = get_db()
    created_at = data.get("created_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
    cursor = db.execute(
        """
        INSERT INTO notifications (
            parameter,
            status,
            current_value,
            threshold_value,
            message,
            created_at,
            is_read,
            is_dismissed
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, 0)
        """,
        (
            data["parameter"],
            data["status"],
            float(data["current_value"]),
            data["threshold_value"],
            data["message"],
            created_at,
        ),
    )
    db.commit()
    return {
        "id": cursor.lastrowid,
        "parameter": data["parameter"],
        "status": data["status"],
        "current_value": float(data["current_value"]),
        "threshold_value": data["threshold_value"],
        "message": data["message"],
        "created_at": created_at,
        "is_read": 0,
        "is_dismissed": 0,
    }


def get_notifications(db=None):
    if db is None:
        db = get_db()
    local_now = datetime.now().astimezone()
    start_of_local_today = datetime.combine(local_now.date(), datetime.min.time()).replace(tzinfo=local_now.tzinfo)
    start_utc_str = start_of_local_today.astimezone(timezone.utc).isoformat(timespec="seconds")

    rows = db.execute(
        """
        SELECT id, parameter, status, current_value, threshold_value, message, created_at, is_read, is_dismissed
        FROM notifications
        WHERE datetime(created_at) >= datetime(?)
        ORDER BY datetime(created_at) DESC, id DESC
        """,
        (start_utc_str,),
    ).fetchall()

    notifications = [
        {
            "id": row["id"],
            "parameter": row["parameter"],
            "status": row["status"],
            "current_value": row["current_value"],
            "threshold_value": row["threshold_value"],
            "message": row["message"],
            "created_at": row["created_at"],
            "is_read": row["is_read"],
            "is_dismissed": row["is_dismissed"],
        }
        for row in rows
    ]

    unread_count = sum(1 for n in notifications if not n["is_read"])
    return notifications, unread_count


def mark_notifications_read(db=None):
    if db is None:
        db = get_db()
    try:
        db.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
        db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to mark notifications read: {e}")
    return get_notifications(db=db)


def clear_all_notifications():
    db = get_db()
    db.execute("DELETE FROM notifications")
    db.commit()
    return [], 0


# ---------------------------------------------------------------------------
# Motor state tracking
# ---------------------------------------------------------------------------

def save_motor_state(metric: str, position: int):
    db = get_db()
    last_updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO motor_state (metric, position, last_updated)
        VALUES (?, ?, ?)
        ON CONFLICT(metric) DO UPDATE SET
            position = excluded.position,
            last_updated = excluded.last_updated
        """,
        (metric, int(position), last_updated),
    )
    db.commit()


def load_motor_state(metric: str):
    try:
        db = get_db()
        row = db.execute(
            "SELECT position FROM motor_state WHERE metric = ?",
            (metric,),
        ).fetchone()
        return int(row["position"]) if row is not None else None
    except Exception:
        return None


def init_motor_state():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS motor_state (
            metric TEXT PRIMARY KEY,
            position INTEGER NOT NULL,
            last_updated TEXT NOT NULL
        )
        """
    )
    db.commit()

    pos = load_motor_state("bleed_valve")
    if pos is None:
        save_motor_state("bleed_valve", 0)

    heat_pos = load_motor_state("heating_valve")
    if heat_pos is None:
        save_motor_state("heating_valve", 0)  # closed for heating

    cool_pos = load_motor_state("cooling_valve")
    if cool_pos is None:
        save_motor_state("cooling_valve", 1)  # open for cooling
