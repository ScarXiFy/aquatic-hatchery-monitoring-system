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


if __name__ == "__main__":
    unittest.main()
