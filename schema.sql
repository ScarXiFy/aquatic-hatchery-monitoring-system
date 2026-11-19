CREATE TABLE IF NOT EXISTS sensor_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    temperature REAL NOT NULL,
    dissolved_oxygen REAL NOT NULL,
    salinity REAL NOT NULL,
    ph REAL NOT NULL
);

-- Reference table for raw sensor readings (temperatures in °C, analog readings in V)
CREATE TABLE IF NOT EXISTS raw_sensor_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    temperature REAL NOT NULL,           -- Raw DS18B20 digital temperature (°C)
    dissolved_oxygen REAL NOT NULL,      -- Raw ADS1115 analog voltage (V)
    salinity REAL NOT NULL,              -- Raw ADS1115 analog voltage (V)
    ph REAL NOT NULL,                    -- Raw ADS1115 analog voltage (V)
    mixing_temperature REAL              -- Raw DS18B20 secondary digital temperature (°C)
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

CREATE INDEX IF NOT EXISTS idx_raw_sensor_readings_timestamp
ON raw_sensor_readings (timestamp);

CREATE INDEX IF NOT EXISTS idx_notifications_created_at
ON notifications (created_at);

CREATE TABLE IF NOT EXISTS motor_state (
    metric TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    last_updated TEXT NOT NULL
);

-- Calibration tables for each of the 5 sensors
CREATE TABLE IF NOT EXISTS sensor_calibrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor TEXT NOT NULL,
    slope REAL NOT NULL,
    offset REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sensor_calibrations_sensor_created
ON sensor_calibrations (sensor, created_at);

CREATE TABLE IF NOT EXISTS ph_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slope REAL NOT NULL,
    offset REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS do_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slope REAL NOT NULL,
    offset REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS salinity_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slope REAL NOT NULL,
    offset REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS temp_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slope REAL NOT NULL,
    offset REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mixing_temp_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slope REAL NOT NULL,
    offset REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS temperature_calibration AS SELECT * FROM temp_calibration;
CREATE VIEW IF NOT EXISTS mixing_temperature_calibration AS SELECT * FROM mixing_temp_calibration;
