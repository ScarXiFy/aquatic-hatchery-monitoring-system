import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calibrate_ph import (
    PHCalibrationLogger,
    export_csv,
    view_history,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_ADC_CHANNEL,
)


class TestPHCalibration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_ph_cal.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_creates_table_and_index(self):
        logger = PHCalibrationLogger(db_path=self.db_path, simulate=True)
        self.assertTrue(self.db_path.exists())

        cursor = logger.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ph_calibration_readings'")
        table = cursor.fetchone()
        self.assertIsNotNone(table)

        cursor.execute("PRAGMA table_info(ph_calibration_readings)")
        columns = [row[1] for row in cursor.fetchall()]
        self.assertIn("id", columns)
        self.assertIn("timestamp", columns)
        self.assertIn("voltage", columns)
        self.assertIn("estimated_ph", columns)
        self.assertIn("notes", columns)
        logger.close()

    def test_record_reading_and_retrieval(self):
        logger = PHCalibrationLogger(db_path=self.db_path, notes="Buffer 7.00", simulate=True)
        reading_id = logger.record_reading(voltage=2.50, timestamp="2026-11-04 12:00:00")
        self.assertEqual(reading_id, 1)
        self.assertEqual(len(logger.session_readings), 1)

        cursor = logger.conn.cursor()
        cursor.execute("SELECT id, timestamp, voltage, estimated_ph, notes FROM ph_calibration_readings WHERE id=1")
        row = cursor.fetchone()
        self.assertEqual(row["timestamp"], "2026-11-04 12:00:00")
        self.assertAlmostEqual(row["voltage"], 2.50)
        self.assertAlmostEqual(row["estimated_ph"], 7.00)
        self.assertEqual(row["notes"], "Buffer 7.00")
        logger.close()

    def test_estimated_ph_formula(self):
        logger = PHCalibrationLogger(db_path=self.db_path, simulate=True)
        # Neutral voltage: 2.50V -> 7.00 pH
        self.assertEqual(logger.compute_estimated_ph(2.50), 7.00)
        # Acidic: 2.50 + 0.18 = 2.68V -> 6.00 pH
        self.assertEqual(logger.compute_estimated_ph(2.68), 6.00)
        # Alkaline: 2.50 - 0.18 = 2.32V -> 8.00 pH
        self.assertEqual(logger.compute_estimated_ph(2.32), 8.00)
        # Clamping
        self.assertEqual(logger.compute_estimated_ph(0.0), 14.0)
        self.assertEqual(logger.compute_estimated_ph(5.0), 0.0)
        logger.close()

    @patch("calibrate_ph.PHCalibrationLogger._init_adc")
    def test_read_voltage_hardware(self, mock_init_adc):
        logger = PHCalibrationLogger(db_path=self.db_path, simulate=False)
        logger.is_hardware_available = True
        logger.adc_service = MagicMock()
        logger.adc_service.read_filtered_voltage.return_value = 2.45678

        v = logger.read_voltage()
        self.assertEqual(v, 2.4568)
        logger.adc_service.read_filtered_voltage.assert_called_once_with(DEFAULT_ADC_CHANNEL)
        logger.close()

    def test_read_voltage_simulation_fallback(self):
        logger = PHCalibrationLogger(db_path=self.db_path, simulate=True)
        self.assertFalse(logger.is_hardware_available)
        v = logger.read_voltage()
        self.assertIsInstance(v, float)
        self.assertTrue(1.5 <= v <= 3.5)
        logger.close()

    def test_export_csv(self):
        logger = PHCalibrationLogger(db_path=self.db_path, notes="Test Run", simulate=True)
        logger.record_reading(voltage=2.50, timestamp="2026-11-04 12:00:00")
        logger.record_reading(voltage=2.14, timestamp="2026-11-04 12:00:30")
        logger.close()

        csv_path = Path(self.temp_dir.name) / "exported_cal.csv"
        export_csv(self.db_path, csv_path)
        self.assertTrue(csv_path.exists())

        with open(csv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 3)  # Header + 2 data rows
        self.assertIn("Timestamp", lines[0])
        self.assertIn("2.5", lines[1])
        self.assertIn("2.14", lines[2])

    def test_view_history(self):
        logger = PHCalibrationLogger(db_path=self.db_path, notes="Test Run", simulate=True)
        logger.record_reading(voltage=2.50, timestamp="2026-11-04 12:00:00")
        logger.close()

        # Check view_history doesn't throw
        view_history(self.db_path, limit=10)

    def test_multiple_runs_append_data(self):
        # First session
        logger1 = PHCalibrationLogger(db_path=self.db_path, notes="Run 1", simulate=True)
        logger1.record_reading(voltage=2.50, timestamp="2026-11-04 12:00:00")
        logger1.close()

        # Second session (program run again)
        logger2 = PHCalibrationLogger(db_path=self.db_path, notes="Run 2", simulate=True)
        logger2.record_reading(voltage=2.45, timestamp="2026-11-04 12:05:00")
        logger2.close()

        # Check that both readings are preserved in the DB
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ph_calibration_readings")
        total = cursor.fetchone()[0]
        self.assertEqual(total, 2)
        conn.close()

    def test_reset_clears_data(self):
        # Create initial data
        logger1 = PHCalibrationLogger(db_path=self.db_path, simulate=True)
        logger1.record_reading(voltage=2.50, timestamp="2026-11-04 12:00:00")
        logger1.close()

        # Run with reset=True
        logger2 = PHCalibrationLogger(db_path=self.db_path, simulate=True, reset=True)
        logger2.record_reading(voltage=2.10, timestamp="2026-11-04 12:10:00")
        logger2.close()

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), id FROM ph_calibration_readings")
        row = cursor.fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
