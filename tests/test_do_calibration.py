import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calibrate_do import (
    DOCalibrationLogger,
    export_csv,
    view_history,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_ADC_CHANNEL,
    DEFAULT_DB_FILE,
)


class TestDOCalibration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_do_cal.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_constants(self):
        self.assertEqual(DEFAULT_INTERVAL_SECONDS, 20)
        self.assertEqual(DEFAULT_ADC_CHANNEL, 2)
        self.assertEqual(DEFAULT_DB_FILE.name, "do_calibration.db")

    def test_init_creates_table_and_index(self):
        logger = DOCalibrationLogger(db_path=self.db_path, simulate=True)
        self.assertTrue(self.db_path.exists())

        cursor = logger.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='do_calibration_readings'")
        table = cursor.fetchone()
        self.assertIsNotNone(table)

        cursor.execute("PRAGMA table_info(do_calibration_readings)")
        columns = [row[1] for row in cursor.fetchall()]
        self.assertIn("id", columns)
        self.assertIn("timestamp", columns)
        self.assertIn("voltage", columns)
        self.assertIn("raw_voltage", columns)
        self.assertIn("estimated_do", columns)
        self.assertIn("notes", columns)
        logger.close()

    def test_record_reading_and_retrieval(self):
        logger = DOCalibrationLogger(db_path=self.db_path, notes="100% saturation air", simulate=True)
        reading_id = logger.record_reading(voltage=2.00, timestamp="2026-11-04 12:00:00")
        self.assertEqual(reading_id, 1)
        self.assertEqual(len(logger.session_readings), 1)

        cursor = logger.conn.cursor()
        cursor.execute(
            """
            SELECT id, timestamp, voltage, raw_voltage, estimated_do, notes
            FROM do_calibration_readings
            WHERE id=1
            """
        )
        row = cursor.fetchone()
        self.assertEqual(row["timestamp"], "2026-11-04 12:00:00")
        self.assertAlmostEqual(row["voltage"], 2.00)
        self.assertAlmostEqual(row["raw_voltage"], 2.00)
        # 2.00 / 3.3 * 10 = 6.06 mg/L
        self.assertAlmostEqual(row["estimated_do"], 6.06)
        self.assertEqual(row["notes"], "100% saturation air")
        logger.close()

    def test_estimated_do_formula(self):
        logger = DOCalibrationLogger(db_path=self.db_path, simulate=True)
        # 0V -> 0 mg/L
        self.assertEqual(logger.compute_estimated_do(0.0), 0.0)
        # 1.65V -> 5.00 mg/L
        self.assertEqual(logger.compute_estimated_do(1.65), 5.00)
        # 3.3V -> 10.00 mg/L
        self.assertEqual(logger.compute_estimated_do(3.3), 10.00)
        # Negative voltage clamped to 0.0
        self.assertEqual(logger.compute_estimated_do(-0.5), 0.0)
        # High voltage clamped to 20.0 max
        self.assertEqual(logger.compute_estimated_do(10.0), 20.0)
        logger.close()

    @patch("calibrate_do.DOCalibrationLogger._init_adc")
    def test_read_voltage_hardware(self, mock_init_adc):
        logger = DOCalibrationLogger(db_path=self.db_path, channel=2, simulate=False)
        logger.is_hardware_available = True
        logger.adc_service = MagicMock()
        logger.adc_service.read_filtered_voltage.return_value = 1.98768

        v = logger.read_voltage()
        self.assertEqual(v, 1.9877)
        logger.adc_service.read_filtered_voltage.assert_called_once_with(2)
        logger.close()

    def test_read_voltage_simulation_fallback(self):
        logger = DOCalibrationLogger(db_path=self.db_path, simulate=True)
        self.assertFalse(logger.is_hardware_available)
        v = logger.read_voltage()
        self.assertIsInstance(v, float)
        self.assertTrue(1.5 <= v <= 2.5)
        logger.close()

    def test_export_csv(self):
        logger = DOCalibrationLogger(db_path=self.db_path, notes="Standard DO test", simulate=True)
        logger.record_reading(voltage=0.15, timestamp="2026-11-04 12:00:00")
        logger.record_reading(voltage=2.10, timestamp="2026-11-04 12:00:20")
        logger.close()

        csv_path = Path(self.temp_dir.name) / "exported_do_cal.csv"
        export_csv(self.db_path, csv_path)
        self.assertTrue(csv_path.exists())

        with open(csv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 3)  # Header + 2 data rows
        self.assertIn("Timestamp", lines[0])
        self.assertIn("Raw Voltage (V)", lines[0])
        self.assertIn("Estimated DO (mg/L)", lines[0])
        self.assertIn("0.15", lines[1])
        self.assertIn("2.1", lines[2])

    def test_view_history(self):
        logger = DOCalibrationLogger(db_path=self.db_path, notes="View Test", simulate=True)
        logger.record_reading(voltage=2.00, timestamp="2026-11-04 12:00:00")
        logger.close()

        # Should execute cleanly without error
        view_history(self.db_path, limit=10)

    def test_multiple_runs_append_data(self):
        # Session 1
        logger1 = DOCalibrationLogger(db_path=self.db_path, notes="Zero solution", simulate=True)
        logger1.record_reading(voltage=0.05, timestamp="2026-11-04 12:00:00")
        logger1.close()

        # Session 2
        logger2 = DOCalibrationLogger(db_path=self.db_path, notes="Air sat", simulate=True)
        logger2.record_reading(voltage=2.05, timestamp="2026-11-04 12:00:20")
        logger2.close()

        # Check both records preserved
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM do_calibration_readings")
        total = cursor.fetchone()[0]
        self.assertEqual(total, 2)
        conn.close()

    def test_reset_clears_data(self):
        # Create initial data
        logger1 = DOCalibrationLogger(db_path=self.db_path, simulate=True)
        logger1.record_reading(voltage=2.00, timestamp="2026-11-04 12:00:00")
        logger1.close()

        # Run with reset=True
        logger2 = DOCalibrationLogger(db_path=self.db_path, simulate=True, reset=True)
        logger2.record_reading(voltage=2.15, timestamp="2026-11-04 12:00:20")
        logger2.close()

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), voltage FROM do_calibration_readings")
        row = cursor.fetchone()
        self.assertEqual(row[0], 1)
        self.assertAlmostEqual(row[1], 2.15)
        conn.close()

    def test_print_summary(self):
        logger = DOCalibrationLogger(db_path=self.db_path, notes="100% saturation air", simulate=True)
        logger.record_reading(voltage=2.00)
        logger.record_reading(voltage=2.02)
        logger.record_reading(voltage=2.04)

        # Ensure summary runs cleanly
        logger.print_summary()
        logger.close()

    def test_cli_help(self):
        from calibrate_do import main
        with patch.object(sys, "argv", ["calibrate_do.py", "--help"]):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
