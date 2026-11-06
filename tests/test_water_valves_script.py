import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
import app.api.piUtils as piUtils
import test_water_valves as twv


class TestWaterValvesScript(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Reset all valve states to False (closed)
        piUtils.coolingValve(False)
        piUtils.heatingValve(False)
        piUtils.mainValve(False)

    def tearDown(self):
        piUtils.coolingValve(False)
        piUtils.heatingValve(False)
        piUtils.mainValve(False)
        self.app_context.pop()

    def test_get_target_duration_positional(self):
        """Test parsing positional duration argument."""
        args = twv.parse_args(["7.5"])
        duration = twv.get_target_duration(args)
        self.assertEqual(duration, 7.5)

    def test_get_target_duration_flag(self):
        """Test parsing --duration flag argument."""
        args = twv.parse_args(["--duration", "12.0"])
        duration = twv.get_target_duration(args)
        self.assertEqual(duration, 12.0)

    def test_get_target_duration_default_when_non_interactive(self):
        """Test default duration when no args provided non-interactively."""
        args = twv.parse_args([])
        with patch("sys.stdin.isatty", return_value=False):
            duration = twv.get_target_duration(args)
            self.assertEqual(duration, twv.DEFAULT_DURATION)

    def test_run_valves_test_cycles_all_valves(self):
        """Test run_valves_test opens all 3 valves, sleeps for duration, and closes all 3 valves."""
        mock_main = MagicMock()
        mock_cooling = MagicMock()
        mock_heating = MagicMock()

        with patch.object(twv, "_set_main_valve", mock_main), \
             patch.object(twv, "_set_cooling_valve", mock_cooling), \
             patch.object(twv, "_set_heating_valve", mock_heating), \
             patch("time.time", side_effect=[0.0, 1.0]), \
             patch("time.sleep", return_value=None):

            twv.run_valves_test(duration=0.1)

            # Check all 3 opened
            mock_main.assert_any_call(True)
            mock_cooling.assert_any_call(True)
            mock_heating.assert_any_call(True)

            # Check all 3 closed
            mock_main.assert_any_call(False)
            mock_cooling.assert_any_call(False)
            mock_heating.assert_any_call(False)

    def test_run_valves_test_closes_on_error(self):
        """Test run_valves_test ensures all valves close even if an exception occurs during test."""
        mock_main = MagicMock()
        mock_cooling = MagicMock()
        mock_heating = MagicMock()

        with patch.object(twv, "_set_main_valve", mock_main), \
             patch.object(twv, "_set_cooling_valve", mock_cooling), \
             patch.object(twv, "_set_heating_valve", mock_heating), \
             patch("time.time", return_value=0.0), \
             patch("time.sleep", side_effect=RuntimeError("Interrupted")):

            with self.assertRaises(RuntimeError):
                twv.run_valves_test(duration=1.0)

            # Closed calls must still be made in finally block
            mock_main.assert_any_call(False)
            mock_cooling.assert_any_call(False)
            mock_heating.assert_any_call(False)

    def test_rpi_gpio_outputs_for_valves(self):
        """Test that in RPi mode, active-low logic sends GPIO.LOW for ON and GPIO.HIGH for OFF."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "GPIO", mock_gpio), \
             patch.object(piUtils, "isRpiPresent", True), \
             patch("time.time", side_effect=[0.0, 1.0]), \
             patch("time.sleep", return_value=None):

            twv.run_valves_test(duration=0.1)

            # Main Valve Pin: 24, Cooling Valve Pin: 25, Heating Valve Pin: 23
            # Active-low ON -> GPIO.LOW
            mock_gpio.output.assert_any_call(24, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(25, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(23, mock_gpio.LOW)

            # Active-low OFF -> GPIO.HIGH
            mock_gpio.output.assert_any_call(24, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(25, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(23, mock_gpio.HIGH)


if __name__ == "__main__":
    unittest.main()
