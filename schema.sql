CREATE TABLE IF NOT EXISTS sensor_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    temperature REAL NOT NULL,
    dissolved_oxygen REAL NOT NULL,
    salinity REAL NOT NULL,
    ph REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS thresholds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric TEXT NOT NULL UNIQUE,
    min_value REAL NOT NULL,
    max_value REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sensor_readings_timestamp
ON sensor_readings (timestamp);
