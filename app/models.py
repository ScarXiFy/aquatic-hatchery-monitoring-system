from datetime import datetime, timedelta, timezone

from app.database import get_db

DEFAULT_THRESHOLDS = {
    "temperature": (24.0, 30.0),
    "dissolved_oxygen": (5.0, 8.5),
    "salinity": (28.0, 35.0),
    "ph": (7.4, 8.4),
}


def row_to_reading(row):
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "temperature": row["temperature"],
        "dissolved_oxygen": row["dissolved_oxygen"],
        "salinity": row["salinity"],
        "ph": row["ph"],
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
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO sensor_readings (
            timestamp,
            temperature,
            dissolved_oxygen,
            salinity,
            ph
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            reading["timestamp"],
            reading["temperature"],
            reading["dissolved_oxygen"],
            reading["salinity"],
            reading["ph"],
        ),
    )
    db.commit()
    return {**reading, "id": cursor.lastrowid}


def get_latest_reading():
    row = get_db().execute(
        """
        SELECT id, timestamp, temperature, dissolved_oxygen, salinity, ph
        FROM sensor_readings
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    return row_to_reading(row) if row else None


def get_history(range_name="day", mode="last_24h"):
    if range_name == "week":
        until = datetime.now(timezone.utc)
        since = until - timedelta(hours=168)
        start_str = since.isoformat(timespec="seconds")
        end_str = until.isoformat(timespec="seconds")
    else:  # "day"
        if mode == "yesterday":
            # Yesterday 12:00:00 AM to Yesterday 11:59:59 PM local time
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

    rows = get_db().execute(
        """
        SELECT id, timestamp, temperature, dissolved_oxygen, salinity, ph
        FROM sensor_readings
        WHERE datetime(timestamp) >= datetime(?)
          AND datetime(timestamp) <= datetime(?)
          AND temperature IS NOT NULL AND temperature != ''
          AND dissolved_oxygen IS NOT NULL AND dissolved_oxygen != ''
          AND salinity IS NOT NULL AND salinity != ''
          AND ph IS NOT NULL AND ph != ''
        ORDER BY datetime(timestamp) ASC, id ASC
        """,
        (start_str, end_str),
    ).fetchall()
    return [row_to_reading(row) for row in rows]


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
                (float(value), float(value))
            )
        elif metric == "dissolved_oxygen_setpoint":
            db.execute(
                "UPDATE thresholds SET min_value = ?, max_value = ? WHERE metric = 'dissolved_oxygen'",
                (float(value), float(value))
            )
        elif metric == "led_intensity":
            db.execute(
                """
                INSERT INTO thresholds (metric, min_value, max_value)
                VALUES ('led_intensity', ?, ?)
                ON CONFLICT(metric) DO UPDATE SET min_value = excluded.min_value, max_value = excluded.max_value
                """,
                (float(value), float(value))
            )
    db.commit()
    return get_control_settings()


def init_system_controls():
    db = get_db()
    ensure_default_thresholds()
    
    # Check if led_intensity exists in thresholds (our initialization marker)
    row = db.execute("SELECT 1 FROM thresholds WHERE metric = 'led_intensity'").fetchone()
    if row:
        return  # Already initialized
        
    latest = get_latest_reading()
    if latest:
        # Initialize using the latest corresponding sensor reading
        temp_init = latest["temperature"]
        do_init = latest["dissolved_oxygen"]
        db.execute(
            "UPDATE thresholds SET min_value = ?, max_value = ? WHERE metric = 'temperature'",
            (float(temp_init), float(temp_init))
        )
        db.execute(
            "UPDATE thresholds SET min_value = ?, max_value = ? WHERE metric = 'dissolved_oxygen'",
            (float(do_init), float(do_init))
        )
    
    # Always insert led_intensity as it is the marker and needs a default
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