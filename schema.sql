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

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parameter TEXT NOT NULL,
    status TEXT NOT NULL,
    current_value REAL NOT NULL,
    threshold_value TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    is_dismissed INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sensor_readings_timestamp
ON sensor_readings (timestamp);

CREATE INDEX IF NOT EXISTS idx_notifications_created_at
ON notifications (created_at);

CREATE TABLE IF NOT EXISTS motor_state (
    metric TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    last_updated TEXT NOT NULL
);

