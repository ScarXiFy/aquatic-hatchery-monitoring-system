import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calibrate_ec import (
    ECCalibrationLogger,
    export_csv,
    view_history,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_ADC_CHANNEL,
    DEFAULT_DB_FILE,
)


class TestECCalibration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_ec_cal.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_constants(self):
        self.assertEqual(DEFAULT_INTERVAL_SECONDS, 20)
        self.assertEqual(DEFAULT_ADC_CHANNEL, 1)
        self.assertEqual(DEFAULT_DB_FILE.name, "ec_calibration.db")

    def test_init_creates_table_and_index(self):
        logger = ECCalibrationLogger(db_path=self.db_path, simulate=True)
        self.assertTrue(self.db_path.exists())

        cursor = logger.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ec_calibration_readings'")
        table = cursor.fetchone()
        self.assertIsNotNone(table)

        cursor.execute("PRAGMA table_info(ec_calibration_readings)")
        columns = [row[1] for row in cursor.fetchall()]
        self.assertIn("id", columns)
        self.assertIn("timestamp", columns)
        self.assertIn("voltage", columns)
        self.assertIn("raw_voltage", columns)
        self.assertIn("estimated_ec", columns)
        self.assertIn("notes", columns)
        logger.close()

    def test_record_reading_and_retrieval(self):
        logger = ECCalibrationLogger(db_path=self.db_path, notes="1413 uS/cm buffer", simulate=True)
        reading_id = logger.record_reading(voltage=1.65, timestamp="2026-11-04 12:00:00")
        self.assertEqual(reading_id, 1)
        self.assertEqual(len(logger.session_readings), 1)

        cursor = logger.conn.cursor()
        cursor.execute(
            """
            SELECT id, timestamp, voltage, raw_voltage, estimated_ec, notes
            FROM ec_calibration_readings
            WHERE id=1
            """
        )
        row = cursor.fetchone()
        self.assertEqual(row["timestamp"], "2026-11-04 12:00:00")
        self.assertAlmostEqual(row["voltage"], 1.65)
        self.assertAlmostEqual(row["raw_voltage"], 1.65)
        self.assertAlmostEqual(row["estimated_ec"], 17.50)
        self.assertEqual(row["notes"], "1413 uS/cm buffer")
        logger.close()

    def test_estimated_ec_formula(self):
        logger = ECCalibrationLogger(db_path=self.db_path, simulate=True)
        # 0V -> 0 ppt
        self.assertEqual(logger.compute_estimated_ec(0.0), 0.0)
        # 1.65V -> 17.50 ppt
        self.assertEqual(logger.compute_estimated_ec(1.65), 17.50)
        # 3.3V -> 35.00 ppt
        self.assertEqual(logger.compute_estimated_ec(3.3), 35.00)
        # Negative voltage clamped to 0.0
        self.assertEqual(logger.compute_estimated_ec(-0.5), 0.0)
        # High voltage clamped to 50.0 max
        self.assertEqual(logger.compute_estimated_ec(5.0), 50.0)
        logger.close()

    @patch("calibrate_ec.ECCalibrationLogger._init_adc")
    def test_read_voltage_hardware(self, mock_init_adc):
        logger = ECCalibrationLogger(db_path=self.db_path, channel=1, simulate=False)
        logger.is_hardware_available = True
        logger.adc_service = MagicMock()
        logger.adc_service.read_filtered_voltage.return_value = 1.45678

        v = logger.read_voltage()
        self.assertEqual(v, 1.4568)
        logger.adc_service.read_filtered_voltage.assert_called_once_with(1)
        logger.close()

    def test_read_voltage_simulation_fallback(self):
        logger = ECCalibrationLogger(db_path=self.db_path, simulate=True)
        self.assertFalse(logger.is_hardware_available)
        v = logger.read_voltage()
        self.assertIsInstance(v, float)
        self.assertTrue(1.0 <= v <= 2.5)
        logger.close()

    def test_export_csv(self):
        logger = ECCalibrationLogger(db_path=self.db_path, notes="Standard 12.88 mS/cm", simulate=True)
        logger.record_reading(voltage=1.65, timestamp="2026-11-04 12:00:00")
        logger.record_reading(voltage=2.10, timestamp="2026-11-04 12:00:20")
        logger.close()

        csv_path = Path(self.temp_dir.name) / "exported_ec_cal.csv"
        export_csv(self.db_path, csv_path)
        self.assertTrue(csv_path.exists())

        with open(csv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 3)  # Header + 2 data rows
        self.assertIn("Timestamp", lines[0])
        self.assertIn("Raw Voltage (V)", lines[0])
        self.assertIn("1.65", lines[1])
        self.assertIn("2.1", lines[2])

    def test_view_history(self):
        logger = ECCalibrationLogger(db_path=self.db_path, notes="View Test", simulate=True)
        logger.record_reading(voltage=1.65, timestamp="2026-11-04 12:00:00")
        logger.close()

        # Should execute cleanly without error
        view_history(self.db_path, limit=10)

    def test_multiple_runs_append_data(self):
        # Session 1
        logger1 = ECCalibrationLogger(db_path=self.db_path, notes="Run 1", simulate=True)
        logger1.record_reading(voltage=1.60, timestamp="2026-11-04 12:00:00")
        logger1.close()

        # Session 2
        logger2 = ECCalibrationLogger(db_path=self.db_path, notes="Run 2", simulate=True)
        logger2.record_reading(voltage=1.65, timestamp="2026-11-04 12:00:20")
        logger2.close()

        # Check both records preserved
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ec_calibration_readings")
        total = cursor.fetchone()[0]
        self.assertEqual(total, 2)
        conn.close()

    def test_reset_clears_data(self):
        # Create initial data
        logger1 = ECCalibrationLogger(db_path=self.db_path, simulate=True)
        logger1.record_reading(voltage=1.65, timestamp="2026-11-04 12:00:00")
        logger1.close()

        # Run with reset=True
        logger2 = ECCalibrationLogger(db_path=self.db_path, simulate=True, reset=True)
        logger2.record_reading(voltage=1.80, timestamp="2026-11-04 12:00:20")
        logger2.close()

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), voltage FROM ec_calibration_readings")
        row = cursor.fetchone()
        self.assertEqual(row[0], 1)
        self.assertAlmostEqual(row[1], 1.80)
        conn.close()

    def test_print_summary(self):
        logger = ECCalibrationLogger(db_path=self.db_path, notes="35 ppt seawater", simulate=True)
        logger.record_reading(voltage=1.60)
        logger.record_reading(voltage=1.65)
        logger.record_reading(voltage=1.70)

        # Ensure summary runs cleanly
        logger.print_summary()
        logger.close()


if __name__ == "__main__":
    unittest.main()
