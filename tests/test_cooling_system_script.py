import csv
import unittest
from unittest.mock import MagicMock, patch
import sys
import tempfile
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import test_cooling_system as tcs


class TestCoolingSystemScript(unittest.TestCase):
    def setUp(self):
        # Reset internal states
        tcs.cooling_active = False
        for pin in tcs.PELTIER_PINS:
            tcs.peltier_states[pin] = False

    def tearDown(self):
        tcs.cooling_active = False
        for pin in tcs.PELTIER_PINS:
            tcs.peltier_states[pin] = False

    def test_peltier_pin_assignments(self):
        """Verify the 8 Peltier pins are strictly 14, 15, 7, 1, 12, 16, 20, 21."""
        expected_pins = [14, 15, 7, 1, 12, 16, 20, 21]
        self.assertEqual(tcs.PELTIER_PINS, expected_pins)
        self.assertEqual(len(tcs.PELTIER_PINS), 8)

    def test_setup_gpio_initializes_peltier_pins_to_high(self):
        """Verify all 8 peltier pins are set up as outputs with initial=GPIO.HIGH."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0
        mock_gpio.OUT = "OUT"
        mock_gpio.BCM = "BCM"

        with patch.object(tcs, "GPIO", mock_gpio), \
             patch.object(tcs, "isRpiPresent", True):

            tcs.setup_gpio()

            mock_gpio.setmode.assert_called_once_with(mock_gpio.BCM)
            setup_pins = [call[0][0] for call in mock_gpio.setup.call_args_list]
            self.assertEqual(setup_pins, tcs.PELTIER_PINS)

            # Ensure all pins were set with initial=HIGH (OFF)
            for call in mock_gpio.setup.call_args_list:
                self.assertEqual(call[1].get("initial"), mock_gpio.HIGH)

    def test_set_peltiers_active_and_inactive(self):
        """Verify set_peltiers drives pins LOW for ON and HIGH for OFF."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(tcs, "GPIO", mock_gpio), \
             patch.object(tcs, "isRpiPresent", True), \
             patch.object(tcs, "RELAY_ON", 0), \
             patch.object(tcs, "RELAY_OFF", 1):

            # Turn ON
            tcs.set_peltiers(True)
            self.assertTrue(tcs.cooling_active)
            for pin in tcs.PELTIER_PINS:
                mock_gpio.output.assert_any_call(pin, 0)
                self.assertTrue(tcs.peltier_states[pin])

            # Turn OFF
            mock_gpio.reset_mock()
            tcs.set_peltiers(False)
            self.assertFalse(tcs.cooling_active)
            for pin in tcs.PELTIER_PINS:
                mock_gpio.output.assert_any_call(pin, 1)
                self.assertFalse(tcs.peltier_states[pin])

    def test_cleanup_hardware_safely_turns_off_peltiers(self):
        """Verify cleanup_hardware de-energizes peltiers and cleans up strictly their pins."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(tcs, "GPIO", mock_gpio), \
             patch.object(tcs, "isRpiPresent", True), \
             patch.object(tcs, "RELAY_OFF", 1):

            tcs.cooling_active = True
            tcs.cleanup_hardware()

            self.assertFalse(tcs.cooling_active)
            mock_gpio.cleanup.assert_called_once_with(tcs.PELTIER_PINS)

    def test_database_initialization_and_logging(self):
        """Verify sqlite database table creation and logging of dual-tank readings."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db_path = Path(tmp.name)
            conn = tcs.init_database(db_path, reset=True)

            # Check table exists
            cursor = conn.cursor()
            table_info = cursor.execute("PRAGMA table_info(cooling_test_readings)").fetchall()
            col_names = [col[1] for col in table_info]
            self.assertIn("id", col_names)
            self.assertIn("timestamp", col_names)
            self.assertIn("main_tank_temp", col_names)
            self.assertIn("mixing_tank_temp", col_names)
            self.assertIn("peltiers_active", col_names)
            self.assertIn("notes", col_names)

            # Insert reading
            row_id = tcs.log_reading(
                conn=conn,
                timestamp="2026-11-18 16:00:00",
                main_temp=25.5,
                mixing_temp=24.8,
                peltiers_active=True,
                notes="Test reading"
            )
            self.assertEqual(row_id, 1)

            # Verify saved record
            row = cursor.execute("SELECT * FROM cooling_test_readings WHERE id=1").fetchone()
            self.assertEqual(row["timestamp"], "2026-11-18 16:00:00")
            self.assertAlmostEqual(row["main_tank_temp"], 25.5)
            self.assertAlmostEqual(row["mixing_tank_temp"], 24.8)
            self.assertEqual(row["peltiers_active"], 1)
            self.assertEqual(row["notes"], "Test reading")

            conn.close()

    def test_check_temperature_sensors_simulation_mode(self):
        """Verify check_temperature_sensors properly marks sensors as detected in simulation."""
        status = tcs.check_temperature_sensors(simulate=True)
        self.assertTrue(status["main"]["detected"])
        self.assertTrue(status["mixing"]["detected"])
        self.assertIsNotNone(status["main"]["sample_temp"])
        self.assertIsNotNone(status["mixing"]["sample_temp"])

    def test_check_temperature_sensors_hardware_detection(self):
        """Verify check_temperature_sensors properly reflects detected vs not detected hardware."""
        mock_ts = MagicMock()
        mock_ts.discover_w1_sensors.return_value = ["28-000000b197bd", "28-000000b260f4"]
        mock_ts.MAIN_TANK_DEVICE_FILE = Path("/fake/main/w1_slave")
        mock_ts.MIXING_TANK_DEVICE_FILE = Path("/fake/mixing/w1_slave")
        mock_ts.MAIN_TANK_DEVICE_ID = "28-000000b197bd"
        mock_ts.MIXING_TANK_DEVICE_ID = "28-000000b260f4"
        mock_ts.is_w1_sensor_available.return_value = True
        mock_ts.read_ds18b20.side_effect = [26.25, 25.80]

        with patch.object(tcs, "get_temp_sensor_service", return_value=mock_ts):
            status = tcs.check_temperature_sensors(simulate=False)
            self.assertTrue(status["main"]["detected"])
            self.assertTrue(status["mixing"]["detected"])
            self.assertEqual(status["main"]["sample_temp"], 26.25)
            self.assertEqual(status["mixing"]["sample_temp"], 25.80)

    def test_prompt_user_continue_choices(self):
        """Verify prompt_user_continue accurately processes Y/N responses."""
        # Auto-yes
        self.assertTrue(tcs.prompt_user_continue(default_yes=True))

        # Interactive Y
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["y"]):
            self.assertTrue(tcs.prompt_user_continue())

        # Interactive YES
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["YES"]):
            self.assertTrue(tcs.prompt_user_continue())

        # Interactive N
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["n"]):
            self.assertFalse(tcs.prompt_user_continue())

        # Interactive NO
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["invalid", "NO"]):
            self.assertFalse(tcs.prompt_user_continue())

    def test_run_cooling_test_aborts_when_user_answers_no(self):
        """Verify that when user inputs 'N', peltiers are never energized and DB is not created."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db_path = Path(tmp.name)
            mock_set_peltiers = MagicMock()

            with patch.object(tcs, "prompt_user_continue", return_value=False), \
                 patch.object(tcs, "set_peltiers", mock_set_peltiers):

                tcs.run_cooling_test(db_path=db_path, simulate=True)

                mock_set_peltiers.assert_not_called()

    def test_run_cooling_test_runs_and_logs_when_user_answers_yes(self):
        """Verify that when user confirms, peltiers are energized, readings logged, and then shut down."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db_path = Path(tmp.name)
            mock_set_peltiers = MagicMock()

            with patch.object(tcs, "prompt_user_continue", return_value=True), \
                 patch.object(tcs, "set_peltiers", mock_set_peltiers):

                tcs.run_cooling_test(
                    db_path=db_path,
                    interval=0.01,
                    max_readings=2,
                    simulate=True
                )

                # Ensure peltiers were turned ON (True), then turned OFF (False)
                mock_set_peltiers.assert_any_call(True)
                mock_set_peltiers.assert_any_call(False)

                # Verify database records were saved
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                rows = cursor.execute("SELECT * FROM cooling_test_readings").fetchall()
                self.assertEqual(len(rows), 2)
                conn.close()

    def test_export_csv_successful(self):
        """Verify export_csv extracts all database readings into a properly formatted CSV."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp_db, \
             tempfile.NamedTemporaryFile(suffix=".csv") as tmp_csv:
            db_path = Path(tmp_db.name)
            csv_path = Path(tmp_csv.name)

            conn = tcs.init_database(db_path, reset=True)
            tcs.log_reading(
                conn=conn,
                timestamp="2026-11-19 12:00:00",
                main_temp=26.50,
                mixing_temp=25.80,
                peltiers_active=True,
                notes="Step 1"
            )
            tcs.log_reading(
                conn=conn,
                timestamp="2026-11-19 12:00:03",
                main_temp=26.35,
                mixing_temp=25.60,
                peltiers_active=True,
                notes=""
            )
            conn.close()

            result = tcs.export_csv(db_path=db_path, output_file=csv_path)
            self.assertEqual(result, csv_path)
            self.assertTrue(csv_path.exists())

            with open(csv_path, mode="r", encoding="utf-8") as f:
                reader = list(csv.reader(f))

            self.assertEqual(len(reader), 3)  # Header + 2 rows
            self.assertEqual(
                reader[0],
                ["ID", "Timestamp", "Main Tank Temp (°C)", "Mixing Tank Temp (°C)", "Peltiers Active", "Notes"]
            )
            self.assertEqual(reader[1], ["1", "2026-11-19 12:00:00", "26.5", "25.8", "1", "Step 1"])
            self.assertEqual(reader[2], ["2", "2026-11-19 12:00:03", "26.35", "25.6", "1", ""])

    def test_export_csv_empty_table(self):
        """Verify export_csv writes header row even if table is empty."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp_db, \
             tempfile.NamedTemporaryFile(suffix=".csv") as tmp_csv:
            db_path = Path(tmp_db.name)
            csv_path = Path(tmp_csv.name)

            conn = tcs.init_database(db_path, reset=True)
            conn.close()

            result = tcs.export_csv(db_path=db_path, output_file=csv_path)
            self.assertEqual(result, csv_path)

            with open(csv_path, mode="r", encoding="utf-8") as f:
                reader = list(csv.reader(f))

            self.assertEqual(len(reader), 1)
            self.assertEqual(
                reader[0],
                ["ID", "Timestamp", "Main Tank Temp (°C)", "Mixing Tank Temp (°C)", "Peltiers Active", "Notes"]
            )

    def test_export_csv_nonexistent_database(self):
        """Verify export_csv returns None and handles missing DB gracefully."""
        missing_db = Path("/nonexistent/cooling_test_missing.db")
        csv_path = Path("/tmp/should_not_exist.csv")
        result = tcs.export_csv(db_path=missing_db, output_file=csv_path)
        self.assertIsNone(result)

    def test_parse_args_export_csv_flags(self):
        """Verify CLI argument parser handles --export-csv, --export, and -e correctly."""
        # Standalone --export-csv without value
        args1 = tcs.parse_args(["--export-csv"])
        self.assertEqual(args1.export_csv, "")

        # --export-csv with explicit path
        args2 = tcs.parse_args(["--export-csv", "my_log.csv"])
        self.assertEqual(args2.export_csv, "my_log.csv")

        # --export alias
        args3 = tcs.parse_args(["--export", "export.csv"])
        self.assertEqual(args3.export_csv, "export.csv")

        # -e short option
        args4 = tcs.parse_args(["-e", "short.csv"])
        self.assertEqual(args4.export_csv, "short.csv")

        # Default when not provided
        args5 = tcs.parse_args([])
        self.assertIsNone(args5.export_csv)

    def test_main_export_csv_mode_exits_without_running_test(self):
        """Verify main() executes export_csv and exits when --export-csv is passed."""
        with patch("test_cooling_system.parse_args") as mock_parse, \
             patch("test_cooling_system.export_csv") as mock_export, \
             patch("test_cooling_system.run_cooling_test") as mock_run:

            # With default target file
            mock_parse.return_value = MagicMock(export_csv="", db="my_cooling.db")
            tcs.main()

            mock_export.assert_called_once_with(
                db_path=Path("my_cooling.db"),
                output_file=Path("my_cooling.csv")
            )
            mock_run.assert_not_called()

            # With custom target file
            mock_export.reset_mock()
            mock_parse.return_value = MagicMock(export_csv="custom.csv", db="my_cooling.db")
            tcs.main()

            mock_export.assert_called_once_with(
                db_path=Path("my_cooling.db"),
                output_file=Path("custom.csv")
            )
            mock_run.assert_not_called()

    def test_run_cooling_test_with_export_csv_path(self):
        """Verify run_cooling_test automatically exports to CSV if export_csv_path is specified."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp_db, \
             tempfile.NamedTemporaryFile(suffix=".csv") as tmp_csv:
            db_path = Path(tmp_db.name)
            csv_path = Path(tmp_csv.name)

            with patch.object(tcs, "prompt_user_continue", return_value=True), \
                 patch.object(tcs, "set_peltiers"):

                tcs.run_cooling_test(
                    db_path=db_path,
                    interval=0.01,
                    max_readings=2,
                    simulate=True,
                    export_csv_path=csv_path
                )

                self.assertTrue(csv_path.exists())
                with open(csv_path, mode="r", encoding="utf-8") as f:
                    reader = list(csv.reader(f))
                self.assertEqual(len(reader), 3)


if __name__ == "__main__":
    unittest.main()
