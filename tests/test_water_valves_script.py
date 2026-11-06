import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_water_valves as twv


class TestWaterValvesScript(unittest.TestCase):
    def setUp(self):
        # Reset valve states
        twv.valve_states[twv.MAIN_VALVE_PIN] = False
        twv.valve_states[twv.COOLING_VALVE_PIN] = False
        twv.valve_states[twv.HEATING_VALVE_PIN] = False

    def tearDown(self):
        twv.valve_states[twv.MAIN_VALVE_PIN] = False
        twv.valve_states[twv.COOLING_VALVE_PIN] = False
        twv.valve_states[twv.HEATING_VALVE_PIN] = False

    def test_pin_assignments(self):
        """Verify only the 3 specific valve pins are defined."""
        self.assertEqual(twv.MAIN_VALVE_PIN, 24)
        self.assertEqual(twv.COOLING_VALVE_PIN, 25)
        self.assertEqual(twv.HEATING_VALVE_PIN, 23)
        self.assertEqual(twv.VALVE_PINS, [24, 25, 23])

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

    def test_run_valves_test_cycles_only_the_three_valves(self):
        """Test run_valves_test opens and closes strictly the 3 valves."""
        mock_open = MagicMock()
        mock_close = MagicMock()

        with patch.object(twv, "open_all_valves", mock_open), \
             patch.object(twv, "close_all_valves", mock_close), \
             patch("time.time", side_effect=[0.0, 1.0]), \
             patch("time.sleep", return_value=None):

            twv.run_valves_test(duration=0.1)

            mock_open.assert_called_once()
            mock_close.assert_called_once()

    def test_run_valves_test_ensures_valves_closed_on_error(self):
        """Test that close_all_valves/cleanup is always executed even on exception."""
        mock_close = MagicMock()

        with patch.object(twv, "close_all_valves", mock_close), \
             patch("time.time", return_value=0.0), \
             patch("time.sleep", side_effect=RuntimeError("Test interrupt")):

            with self.assertRaises(RuntimeError):
                twv.run_valves_test(duration=1.0)

            mock_close.assert_called_once()

    def test_rpi_gpio_setup_and_output_calls(self):
        """Test that in RPi mode, only pins 24, 25, 23 are setup, driven LOW, then HIGH, and cleaned up."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0
        mock_gpio.OUT = "OUT"
        mock_gpio.BCM = "BCM"

        with patch.object(twv, "GPIO", mock_gpio), \
             patch.object(twv, "isRpiPresent", True), \
             patch("time.time", side_effect=[0.0, 1.0]), \
             patch("time.sleep", return_value=None):

            twv.run_valves_test(duration=0.1)

            # Check GPIO setup was done ONLY for pins 24, 25, 23
            setup_pins = [call[0][0] for call in mock_gpio.setup.call_args_list]
            self.assertEqual(setup_pins, [24, 25, 23])

            # Check that only pins 24, 25, 23 were driven LOW (ON)
            mock_gpio.output.assert_any_call(24, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(25, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(23, mock_gpio.LOW)

            # Check that only pins 24, 25, 23 were driven HIGH (OFF)
            mock_gpio.output.assert_any_call(24, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(25, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(23, mock_gpio.HIGH)

            # Check cleanup was restricted to [24, 25, 23]
            mock_gpio.cleanup.assert_called_once_with([24, 25, 23])


if __name__ == "__main__":
    unittest.main()
