import csv
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from read_sensor import (
    CalibratedSensorReader,
    SENSOR_METADATA,
    export_csv,
    view_history,
)


class TestCalibratedSensorReader(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_calibrated.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_normalize_sensor_key(self):
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("1"), "ph")
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("pH"), "ph")
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("ph_sensor"), "ph")
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("2"), "do")
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("DO"), "do")
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("dissolved_oxygen"), "do")
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("3"), "salinity")
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("salinity"), "salinity")
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("ec"), "salinity")
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("4"), "temp")
        self.assertEqual(CalibratedSensorReader._normalize_sensor_key("temperature"), "temp")

        with self.assertRaises(ValueError):
            CalibratedSensorReader._normalize_sensor_key("invalid_sensor")

    def test_ph_calibration_calculation(self):
        reader = CalibratedSensorReader(
            sensor_key="ph",
            db_path=self.db_path,
            simulate=True,
        )
        # Formula: y = -6.41401 * V + 17.29167
        # At V = 1.6045V:
        # -6.41401 * 1.6045 + 17.29167 = 7.00039 -> 7.00
        val, t = reader.calculate_calibrated_value(1.6045)
        self.assertEqual(val, 7.00)
        self.assertIsNone(t)

        # Clamping
        val_high, _ = reader.calculate_calibrated_value(0.5)
        self.assertEqual(val_high, 14.0)

        val_low, _ = reader.calculate_calibrated_value(4.0)
        self.assertEqual(val_low, 0.0)
        reader.close()

    def test_do_calibration_calculation(self):
        reader = CalibratedSensorReader(
            sensor_key="do",
            db_path=self.db_path,
            simulate=True,
        )
        # Formula: y = 5.93086 * V + 0.3273368
        # At V = 1.0V -> 6.258 -> 6.26 mg/L
        val, t = reader.calculate_calibrated_value(1.0)
        self.assertEqual(val, 6.26)
        self.assertIsNone(t)

        # Clamping
        val_high, _ = reader.calculate_calibrated_value(5.0)
        self.assertEqual(val_high, 20.0)

        val_low, _ = reader.calculate_calibrated_value(-1.0)
        self.assertEqual(val_low, 0.0)
        reader.close()

    @patch("read_sensor.CalibratedSensorReader.get_live_temp_for_compensation")
    def test_salinity_calibration_calculation(self, mock_temp):
        mock_temp.return_value = 30.0
        reader = CalibratedSensorReader(
            sensor_key="salinity",
            db_path=self.db_path,
            simulate=True,
        )
        # At 30C, alpha=0.02:
        # denom = 1.0 + 0.02 * (30 - 25) = 1.10
        # V = 0.88V, V_0 = 0.00255V
        # live_v25 = ((0.88 - 0.00255) / 1.10) + 0.00255 = 0.8002318
        # salinity = 38.265306 * (0.8002318 - 0.00255) = 30.5235... -> 30.52 ppt
        val, comp_temp = reader.calculate_calibrated_value(0.88)
        self.assertEqual(val, 30.52)
        self.assertEqual(comp_temp, 30.0)

        # At 0.00255V (0 ppt)
        val_zero, _ = reader.calculate_calibrated_value(0.00255)
        self.assertEqual(val_zero, 0.0)

        # Clamping
        val_high, _ = reader.calculate_calibrated_value(5.0)
        self.assertEqual(val_high, 50.0)
        reader.close()

    def test_temp_sensor_reading(self):
        reader = CalibratedSensorReader(
            sensor_key="temp",
            db_path=self.db_path,
            simulate=True,
        )
        val, t = reader.calculate_calibrated_value(26.45)
        self.assertEqual(val, 26.45)
        self.assertIsNone(t)
        reader.close()

    def test_db_recording_and_reset(self):
        reader = CalibratedSensorReader(
            sensor_key="ph",
            db_path=self.db_path,
            notes="Buffer Test",
            simulate=True,
        )
        rec_id = reader.record_reading(raw_val=1.6045, cal_val=7.00, timestamp="2026-11-05 12:00:00")
        self.assertEqual(rec_id, 1)

        # Verify DB content
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT sensor, raw_value, calibrated_value, unit, notes FROM calibrated_sensor_readings WHERE id = 1")
        row = cursor.fetchone()
        conn.close()

        self.assertEqual(row[0], "ph")
        self.assertAlmostEqual(row[1], 1.6045)
        self.assertAlmostEqual(row[2], 7.00)
        self.assertEqual(row[3], "pH")
        self.assertEqual(row[4], "Buffer Test")
        reader.close()

        # Test reset
        reader_reset = CalibratedSensorReader(
            sensor_key="ph",
            db_path=self.db_path,
            simulate=True,
            reset=True,
        )
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM calibrated_sensor_readings")
        count = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)
        reader_reset.close()

    def test_view_history_and_export_csv(self):
        reader = CalibratedSensorReader(
            sensor_key="do",
            db_path=self.db_path,
            notes="DO Export Test",
            simulate=True,
        )
        reader.record_reading(raw_val=1.0, cal_val=6.26, timestamp="2026-11-05 12:00:00")
        reader.record_reading(raw_val=2.0, cal_val=12.19, timestamp="2026-11-05 12:00:05")
        reader.close()

        # Capture view_history output
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            view_history(self.db_path, sensor="do")
        output = buf.getvalue()
        self.assertIn("RECENT CALIBRATED READINGS", output)
        self.assertIn("6.26 mg/L", output)
        self.assertIn("12.19 mg/L", output)

        # Test export_csv
        csv_file = Path(self.temp_dir.name) / "exported_readings.csv"
        export_csv(self.db_path, csv_file, sensor="do")
        self.assertTrue(csv_file.exists())

        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        self.assertEqual(len(rows), 3)  # Header + 2 data rows
        self.assertEqual(rows[0][4], "Calibrated Value")
        self.assertEqual(rows[1][4], "6.26")
        self.assertEqual(rows[2][4], "12.19")


if __name__ == "__main__":
    unittest.main()
