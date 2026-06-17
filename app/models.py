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


def get_history(range_name="day"):
    hours = 168 if range_name == "week" else 24
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = get_db().execute(
        """
        SELECT id, timestamp, temperature, dissolved_oxygen, salinity, ph
        FROM sensor_readings
        WHERE timestamp >= ?
        ORDER BY timestamp ASC, id ASC
        """,
        (since.isoformat(timespec="seconds"),),
    ).fetchall()
    return [row_to_reading(row) for row in rows]


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

def update_threshold_field(metric, field, value):
    if field not in ("min_value", "max_value"):
        raise ValueError("Invalid threshold field")

    db = get_db()

    db.execute(
        f"""
        UPDATE thresholds
        SET {field} = ?
        WHERE metric = ?
        """,
        (value, metric)
    )

    db.commit()
