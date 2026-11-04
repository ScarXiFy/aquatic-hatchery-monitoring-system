import csv
import math
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calibrate_temp import (
    TempCalibrationLogger,
    discover_w1_sensors,
    export_csv,
    parse_w1_slave_data,
    prompt_sensor_selection,
    view_history,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_DB_FILE,
    SENSOR_CONFIGS,
)


class TestTempCalibration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_temp_cal.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_creates_table_and_indexes(self):
        logger = TempCalibrationLogger(sensor_type="main", db_path=self.db_path, simulate=True)
        self.assertTrue(self.db_path.exists())

        cursor = logger.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='temp_calibration_readings'")
        table = cursor.fetchone()
        self.assertIsNotNone(table)

        cursor.execute("PRAGMA table_info(temp_calibration_readings)")
        columns = [row[1] for row in cursor.fetchall()]
        self.assertIn("id", columns)
        self.assertIn("timestamp", columns)
        self.assertIn("sensor_name", columns)
        self.assertIn("sensor_id", columns)
        self.assertIn("temperature_c", columns)
        self.assertIn("temperature_f", columns)
        self.assertIn("raw_data", columns)
        self.assertIn("notes", columns)
        logger.close()

    def test_sensor_selection_main_and_mixing(self):
        # Test Main Tank
        logger_main = TempCalibrationLogger(sensor_type="main", db_path=self.db_path, simulate=True)
        self.assertEqual(logger_main.sensor_key, "main")
        self.assertEqual(logger_main.sensor_label, "Main Tank Sensor")
        self.assertEqual(logger_main.device_id, SENSOR_CONFIGS["main"]["default_device_id"])
        logger_main.close()

        # Test Mixing Tank
        logger_mix = TempCalibrationLogger(sensor_type="mixing", db_path=self.db_path, simulate=True)
        self.assertEqual(logger_mix.sensor_key, "mixing")
        self.assertEqual(logger_mix.sensor_label, "Mixing Tank Sensor")
        self.assertEqual(logger_mix.device_id, SENSOR_CONFIGS["mixing"]["default_device_id"])
        logger_mix.close()

        # Test Numeric inputs "1" and "2"
        logger_1 = TempCalibrationLogger(sensor_type="1", db_path=self.db_path, simulate=True)
        self.assertEqual(logger_1.sensor_key, "main")
        logger_1.close()

        logger_2 = TempCalibrationLogger(sensor_type="2", db_path=self.db_path, simulate=True)
        self.assertEqual(logger_2.sensor_key, "mixing")
        logger_2.close()

        # Test custom device ID override
        custom_id = "28-000099999999"
        logger_custom = TempCalibrationLogger(
            sensor_type="main", device_id=custom_id, db_path=self.db_path, simulate=True
        )
        self.assertEqual(logger_custom.device_id, custom_id)
        logger_custom.close()

    def test_parse_w1_slave_data_valid(self):
        lines = [
            "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n",
            "72 01 4b 46 7f ff 0e 10 57 t=25125\n",
        ]
        temp_c, raw = parse_w1_slave_data(lines)
        self.assertEqual(temp_c, 25.12)
        self.assertIn("YES", raw)
        self.assertIn("t=25125", raw)

    def test_parse_w1_slave_data_crc_error(self):
        lines = [
            "72 01 4b 46 7f ff 0e 10 57 : crc=57 NO\n",
            "72 01 4b 46 7f ff 0e 10 57 t=25125\n",
        ]
        temp_c, raw = parse_w1_slave_data(lines)
        self.assertIsNone(temp_c)

    def test_parse_w1_slave_data_malformed(self):
        self.assertEqual(parse_w1_slave_data([]), (None, ""))
        self.assertEqual(parse_w1_slave_data(["only one line\n"]), (None, ""))
        self.assertEqual(parse_w1_slave_data(["line 1 YES\n", "no temp pos here\n"]), (None, "line 1 YES | no temp pos here"))

    def test_read_temperature_hardware_success(self):
        w1_data = "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=26500\n"
        logger = TempCalibrationLogger(sensor_type="main", db_path=self.db_path, simulate=False)
        logger.is_hardware_available = True
        logger.device_file = Path(self.temp_dir.name) / "mock_w1_slave"

        with open(logger.device_file, "w") as f:
            f.write(w1_data)

        temp_c, temp_f, raw = logger.read_temperature()
        self.assertEqual(temp_c, 26.50)
        self.assertEqual(temp_f, 79.70)
        self.assertIn("t=26500", raw)
        logger.close()

    def test_read_temperature_simulation_fallback(self):
        logger = TempCalibrationLogger(sensor_type="main", db_path=self.db_path, simulate=True)
        self.assertFalse(logger.is_hardware_available)
        temp_c, temp_f, raw = logger.read_temperature()
        self.assertIsInstance(temp_c, float)
        self.assertIsInstance(temp_f, float)
        self.assertTrue(24.0 <= temp_c <= 28.0)
        self.assertAlmostEqual(temp_f, round(temp_c * 9.0 / 5.0 + 32.0, 2))
        self.assertIn("SIMULATED", raw)
        logger.close()

    def test_record_reading_and_retrieval(self):
        logger = TempCalibrationLogger(
            sensor_type="main",
            db_path=self.db_path,
            notes="Calibration test at 25C",
            reference_temp=25.0,
            simulate=True,
        )
        rec_id = logger.record_reading(
            temp_c=25.25,
            temp_f=77.45,
            raw_data="t=25250",
            timestamp="2026-11-04 13:00:00",
        )
        self.assertEqual(rec_id, 1)
        self.assertEqual(len(logger.session_readings), 1)

        cursor = logger.conn.cursor()
        cursor.execute("SELECT * FROM temp_calibration_readings WHERE id=1")
        row = cursor.fetchone()
        self.assertEqual(row["timestamp"], "2026-11-04 13:00:00")
        self.assertEqual(row["sensor_name"], "main")
        self.assertEqual(row["sensor_id"], SENSOR_CONFIGS["main"]["default_device_id"])
        self.assertAlmostEqual(row["temperature_c"], 25.25)
        self.assertAlmostEqual(row["temperature_f"], 77.45)
        self.assertEqual(row["raw_data"], "t=25250")
        self.assertEqual(row["notes"], "Calibration test at 25C")
        logger.close()

    def test_export_csv(self):
        logger = TempCalibrationLogger(sensor_type="mixing", db_path=self.db_path, simulate=True)
        logger.record_reading(temp_c=26.10, temp_f=78.98, raw_data="t=26100", timestamp="2026-11-04 13:00:00")
        logger.record_reading(temp_c=26.20, temp_f=79.16, raw_data="t=26200", timestamp="2026-11-04 13:00:20")
        logger.close()

        csv_path = Path(self.temp_dir.name) / "exported_temp.csv"
        export_csv(self.db_path, csv_path)
        self.assertTrue(csv_path.exists())

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))
        self.assertEqual(len(reader), 3)  # Header + 2 data rows
        self.assertEqual(reader[0][0], "ID")
        self.assertEqual(reader[1][2], "mixing")
        self.assertEqual(reader[1][4], "26.1")
        self.assertEqual(reader[2][4], "26.2")

    def test_view_history(self):
        logger = TempCalibrationLogger(sensor_type="main", db_path=self.db_path, simulate=True)
        logger.record_reading(temp_c=25.0, temp_f=77.0, raw_data="t=25000", timestamp="2026-11-04 13:00:00")
        logger.close()

        # Should execute without errors
        view_history(self.db_path, sensor_filter=None, limit=10)
        view_history(self.db_path, sensor_filter="main", limit=10)
        view_history(self.db_path, sensor_filter="mixing", limit=10)

    def test_multiple_runs_append_data(self):
        # Session 1: Calibrate main sensor
        logger1 = TempCalibrationLogger(sensor_type="main", db_path=self.db_path, simulate=True)
        logger1.record_reading(temp_c=25.1, temp_f=77.18, timestamp="2026-11-04 13:00:00")
        logger1.close()

        # Session 2: Calibrate mixing sensor in same DB
        logger2 = TempCalibrationLogger(sensor_type="mixing", db_path=self.db_path, simulate=True)
        logger2.record_reading(temp_c=26.5, temp_f=79.70, timestamp="2026-11-04 13:10:00")
        logger2.close()

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM temp_calibration_readings")
        count = cursor.fetchone()[0]
        self.assertEqual(count, 2)
        conn.close()

    def test_reset_flag(self):
        # Create reading
        logger1 = TempCalibrationLogger(sensor_type="main", db_path=self.db_path, simulate=True)
        logger1.record_reading(temp_c=25.0, temp_f=77.0, timestamp="2026-11-04 13:00:00")
        logger1.close()

        # Run with reset=True
        logger2 = TempCalibrationLogger(sensor_type="main", db_path=self.db_path, simulate=True, reset=True)
        logger2.record_reading(temp_c=24.8, temp_f=76.64, timestamp="2026-11-04 13:20:00")
        logger2.close()

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), id FROM temp_calibration_readings")
        row = cursor.fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], 1)
        conn.close()

    def test_default_interval_is_twenty_seconds(self):
        self.assertEqual(DEFAULT_INTERVAL_SECONDS, 20)
        logger = TempCalibrationLogger(sensor_type="main", db_path=self.db_path, simulate=True)
        self.assertEqual(logger.interval, 20)
        logger.close()


if __name__ == "__main__":
    unittest.main()
