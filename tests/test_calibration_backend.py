import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
import sys
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.models import (
    DEFAULT_CALIBRATIONS,
    SALINITY_TEMP_ALPHA,
    SENSOR_TABLE_MAP,
    SENSOR_NORMALIZATION,
    ensure_default_calibrations,
    get_latest_calibration,
    get_all_latest_calibrations,
    add_sensor_calibration,
    get_calibration_history,
    apply_calibration,
    normalize_salinity_voltage_to_25c,
    create_sensor_reading,
    get_latest_reading,
    get_history,
    row_to_reading,
)


class TestCalibrationBackend(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_hatchery.db"
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

        # Execute schema
        schema_path = BASE_DIR / "schema.sql"
        with schema_path.open("r", encoding="utf-8") as f:
            self.conn.executescript(f.read())
        self.conn.commit()

        # Patch get_db to return self.conn
        import app.models as models
        import app.database as database
        self._orig_get_db = models.get_db
        models.get_db = lambda: self.conn
        database.get_db = lambda: self.conn

    def tearDown(self):
        import app.models as models
        import app.database as database
        models.get_db = self._orig_get_db
        database.get_db = self._orig_get_db
        self.conn.close()
        self.temp_dir.cleanup()

    def test_schema_creates_tables_and_preserves_sensor_readings(self):
        """Verify that sensor_readings is preserved and raw_sensor_readings and calibration tables exist."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        # Verify old table is preserved
        self.assertIn("sensor_readings", tables)

        # Verify new reference table exists
        self.assertIn("raw_sensor_readings", tables)

        # Verify calibration tables exist for all 6 sensors
        self.assertIn("sensor_calibrations", tables)
        self.assertIn("ph_calibration", tables)
        self.assertIn("do_calibration", tables)
        self.assertIn("salinity_calibration", tables)
        self.assertIn("temp_calibration", tables)
        self.assertIn("mixing_temp_calibration", tables)
        self.assertIn("heating_temp_calibration", tables)

    def test_default_starting_calibration_values(self):
        """Verify default starting slope and offset values match previously hardcoded values."""
        ensure_default_calibrations(self.conn)
        calibrations = get_all_latest_calibrations(self.conn)

        # pH defaults
        self.assertAlmostEqual(calibrations["ph"]["slope"], -6.41401, places=5)
        self.assertAlmostEqual(calibrations["ph"]["offset"], 17.29167, places=5)

        # DO defaults
        self.assertAlmostEqual(calibrations["do"]["slope"], 5.93086, places=5)
        self.assertAlmostEqual(calibrations["do"]["offset"], 0.3273368, places=6)

        # Salinity defaults
        self.assertAlmostEqual(calibrations["salinity"]["slope"], 38.265306, places=5)
        self.assertAlmostEqual(calibrations["salinity"]["offset"], 0.0, places=5)

        # Main tank temperature defaults
        self.assertAlmostEqual(calibrations["temp"]["slope"], 1.0, places=5)
        self.assertAlmostEqual(calibrations["temp"]["offset"], 0.0, places=5)

        # Mixing tank temperature defaults
        self.assertAlmostEqual(calibrations["mixing_temp"]["slope"], 1.0, places=5)
        self.assertAlmostEqual(calibrations["mixing_temp"]["offset"], 0.0, places=5)

    def test_raw_readings_stored_in_new_reference_table(self):
        """Verify that create_sensor_reading stores raw voltages and raw temperatures into raw_sensor_readings."""
        ensure_default_calibrations(self.conn)

        raw_data = {
            "timestamp": "2026-11-19T08:00:00+00:00",
            "raw_temperature": 26.25,
            "raw_dissolved_oxygen": 1.162,
            "raw_salinity": 0.852,
            "raw_ph": 1.605,
            "raw_mixing_temperature": 25.90,
        }
        reading = create_sensor_reading(raw_data)

        # Verify reading returned has both calibrated values and raw values
        self.assertEqual(reading["raw_ph"], 1.605)
        self.assertEqual(reading["raw_dissolved_oxygen"], 1.162)
        self.assertEqual(reading["raw_salinity"], 0.852)
        self.assertEqual(reading["raw_temperature"], 26.25)
        self.assertEqual(reading["raw_mixing_temperature"], 25.90)

        # Check raw database record
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM raw_sensor_readings WHERE id = ?", (reading["id"],))
        db_row = dict(cursor.fetchone())

        self.assertAlmostEqual(db_row["ph"], 1.605)
        self.assertAlmostEqual(db_row["dissolved_oxygen"], 1.162)
        self.assertAlmostEqual(db_row["salinity"], 0.852)
        self.assertAlmostEqual(db_row["temperature"], 26.25)
        self.assertAlmostEqual(db_row["mixing_temperature"], 25.90)

    def test_new_calibration_does_not_overwrite_previous(self):
        """Verify that adding a new calibration equation appends a new row and preserves history."""
        ensure_default_calibrations(self.conn)

        # Default pH calibration should be present
        history_before = get_calibration_history("ph", db=self.conn)
        self.assertEqual(len(history_before), 1)
        self.assertAlmostEqual(history_before[0]["slope"], -6.41401, places=5)

        # Add a new calibration equation
        new_cal = add_sensor_calibration("ph", slope=-6.80, offset=18.10, db=self.conn)
        self.assertEqual(new_cal["sensor"], "ph")
        self.assertEqual(new_cal["slope"], -6.80)
        self.assertEqual(new_cal["offset"], 18.10)

        # Check that previous record was not overwritten
        history_after = get_calibration_history("ph", db=self.conn)
        self.assertEqual(len(history_after), 2)
        # Most recent first
        self.assertEqual(history_after[0]["slope"], -6.80)
        self.assertAlmostEqual(history_after[1]["slope"], -6.41401, places=5)

        # Check dedicated table also has 2 records
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ph_calibration")
        self.assertEqual(cursor.fetchone()[0], 2)

    def test_dynamic_calibration_application(self):
        """Verify that changing calibration equations in DB immediately changes the calibrated value from raw readings."""
        ensure_default_calibrations(self.conn)

        # Record a raw reading with pH voltage = 1.600 V
        raw_data = {
            "timestamp": "2026-11-19T09:00:00+00:00",
            "raw_temperature": 26.0,
            "raw_dissolved_oxygen": 1.15,
            "raw_salinity": 0.85,
            "raw_ph": 1.600,
            "raw_mixing_temperature": 26.0,
        }
        create_sensor_reading(raw_data)

        # Under default calibration (-6.41401 * 1.600 + 17.29167 = 7.029 -> 7.03)
        reading1 = get_latest_reading()
        self.assertAlmostEqual(reading1["ph"], 7.03, places=2)

        # Now commissioner adds a new calibration: slope = -6.50, offset = 18.00
        # Expected: -6.50 * 1.600 + 18.00 = 7.60 pH
        add_sensor_calibration("ph", slope=-6.50, offset=18.00, db=self.conn)

        # Without creating any new reading, reading latest should now dynamically evaluate to 7.60!
        reading2 = get_latest_reading()
        self.assertAlmostEqual(reading2["ph"], 7.60, places=2)

        # And raw voltage remains strictly 1.600 V
        self.assertEqual(reading2["raw_ph"], 1.600)

    def test_temperature_calibration_offset(self):
        """Verify digital temperature calibration (e.g. single-point reference offset)."""
        ensure_default_calibrations(self.conn)

        raw_data = {
            "timestamp": "2026-11-19T09:30:00+00:00",
            "raw_temperature": 25.50,
            "raw_dissolved_oxygen": 1.15,
            "raw_salinity": 0.85,
            "raw_ph": 1.60,
            "raw_mixing_temperature": 25.00,
        }
        create_sensor_reading(raw_data)

        # Under default (slope=1.0, offset=0.0) -> temp is 25.50
        latest1 = get_latest_reading()
        self.assertEqual(latest1["temperature"], 25.50)

        # User calibrates temperature with offset +0.35°C (slope=1.0, offset=0.35)
        add_sensor_calibration("temp", slope=1.0, offset=0.35, db=self.conn)

        latest2 = get_latest_reading()
        self.assertEqual(latest2["temperature"], 25.85)
        self.assertEqual(latest2["raw_temperature"], 25.50)

    def test_sensor_aliases_and_normalization(self):
        """Verify that sensor aliases like 'temperature', 'dissolved_oxygen', 'ec' resolve correctly."""
        ensure_default_calibrations(self.conn)

        add_sensor_calibration("dissolved_oxygen", slope=6.0, offset=0.5, db=self.conn)
        cal = get_latest_calibration("do", db=self.conn)
        self.assertEqual(cal["slope"], 6.0)
        self.assertEqual(cal["offset"], 0.5)

        add_sensor_calibration("ec", slope=40.0, offset=0.1, db=self.conn)
        cal_sal = get_latest_calibration("salinity", db=self.conn)
        self.assertEqual(cal_sal["slope"], 40.0)
        self.assertEqual(cal_sal["offset"], 0.1)

    def test_history_applies_calibrations(self):
        """Verify that get_history queries raw_sensor_readings and applies calibrations."""
        ensure_default_calibrations(self.conn)

        now = datetime.now(timezone.utc)
        ts1 = now.isoformat(timespec="seconds")

        create_sensor_reading({
            "timestamp": ts1,
            "raw_temperature": 26.0,
            "raw_dissolved_oxygen": 1.20,
            "raw_salinity": 0.80,
            "raw_ph": 1.50,
            "raw_mixing_temperature": 26.0,
        })

        history = get_history(range_name="day", mode="last_24h")
        self.assertGreaterEqual(len(history), 1)
        item = history[-1]
        self.assertIn("raw", item)
        self.assertEqual(item["raw"]["ph"], 1.50)
        self.assertAlmostEqual(item["ph"], round(-6.41401 * 1.50 + 17.29167, 2), places=2)

    def test_salinity_voltage_normalized_to_25c_using_main_tank_ds18b20(self):
        """Verify salinity voltage is normalized to 25C via V_25 = V_raw / (1 + alpha * (T - 25))."""
        ensure_default_calibrations(self.conn)

        # At 25.0°C, V_25 == V_raw
        self.assertAlmostEqual(normalize_salinity_voltage_to_25c(0.88, 25.0), 0.88, places=6)

        # At 30.0°C with alpha=0.02: V_25 = 0.88 / (1 + 0.02 * (30 - 25)) = 0.88 / 1.10 = 0.80
        self.assertEqual(SALINITY_TEMP_ALPHA, 0.02)
        self.assertAlmostEqual(normalize_salinity_voltage_to_25c(0.88, 30.0), 0.80, places=6)

        # create_sensor_reading exposes salinity_voltage_25c using main tank DS18B20 temperature
        raw_data = {
            "timestamp": "2026-11-19T10:00:00+00:00",
            "raw_temperature": 30.0,
            "raw_dissolved_oxygen": 1.15,
            "raw_salinity": 0.88,
            "raw_ph": 1.60,
            "raw_mixing_temperature": 22.0,  # Mixing tank temp must NOT be used for salinity
        }
        reading = create_sensor_reading(raw_data)
        self.assertAlmostEqual(reading["salinity_voltage_25c"], 0.80, places=4)
        self.assertAlmostEqual(reading["salinity_temp_c"], 30.0, places=2)
        # Calibrated salinity = 38.265306 * 0.80 + 0.0 = 30.6122 -> 30.61 ppt
        self.assertAlmostEqual(reading["salinity"], round(38.265306 * 0.80, 2), places=2)

        # get_latest_reading (row_to_reading) also exposes the same normalized 25°C voltage
        latest = get_latest_reading()
        self.assertAlmostEqual(latest["salinity_voltage_25c"], 0.80, places=4)
        self.assertAlmostEqual(latest["salinity_temp_c"], 30.0, places=2)
        self.assertAlmostEqual(latest["salinity"], round(38.265306 * 0.80, 2), places=2)

    def test_heating_tank_temp_calibration_and_sensor_id(self):
        """Verify heating_temp calibration works end-to-end and default sensor ID is 28-7cf4d4452c40."""
        from app.services.temp_sensor import HEATING_TANK_DEVICE_ID

        self.assertEqual(HEATING_TANK_DEVICE_ID, "28-7cf4d4452c40")

        ensure_default_calibrations(self.conn)
        cal = get_latest_calibration("heating_temp", db=self.conn)
        self.assertAlmostEqual(cal["slope"], 1.0, places=5)
        self.assertAlmostEqual(cal["offset"], 0.0, places=5)

        raw_data = {
            "timestamp": "2026-11-19T11:00:00+00:00",
            "raw_temperature": 26.0,
            "raw_dissolved_oxygen": 1.15,
            "raw_salinity": 0.85,
            "raw_ph": 1.60,
            "raw_mixing_temperature": 25.5,
            "raw_heating_tank_temperature": 39.25,
        }
        reading = create_sensor_reading(raw_data)
        self.assertAlmostEqual(reading["heating_tank_temperature"], 39.25, places=2)
        self.assertAlmostEqual(reading["raw_heating_tank_temperature"], 39.25, places=2)
        self.assertAlmostEqual(reading["raw"]["heating_tank_temperature"], 39.25, places=2)

        # Apply +0.75C offset calibration to heating_temp
        add_sensor_calibration("heating_temp", slope=1.0, offset=0.75, db=self.conn)
        latest = get_latest_reading()
        self.assertAlmostEqual(latest["heating_tank_temperature"], 40.00, places=2)
        self.assertAlmostEqual(latest["raw_heating_tank_temperature"], 39.25, places=2)


if __name__ == "__main__":
    unittest.main()
