import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_aerator_valves as tav


class TestAeratorValvesScript(unittest.TestCase):
    def setUp(self):
        tav.all_off()

    def tearDown(self):
        tav.all_off()

    def test_pin_definitions(self):
        """Verify pins are strictly 10, 9, 11 for solenoids and 14 for the fan."""
        self.assertEqual(tav.SOLENOID_PIN_1, 10)
        self.assertEqual(tav.SOLENOID_PIN_2, 9)
        self.assertEqual(tav.SOLENOID_PIN_3, 11)
        self.assertEqual(tav.FAN_PIN, 14)
        self.assertEqual(tav.ALL_PINS, [10, 9, 11, 14])

    def test_parse_args(self):
        """Test positional and flag step duration parsing."""
        args1 = tav.parse_args(["4.5"])
        self.assertEqual(tav.get_step_duration(args1), 4.5)

        args2 = tav.parse_args(["--step", "2.5"])
        self.assertEqual(tav.get_step_duration(args2), 2.5)

        args3 = tav.parse_args([])
        with patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(tav.get_step_duration(args3), tav.DEFAULT_STEP_DURATION)

    def test_fan_responsiveness_rules(self):
        """Test fan enables when >= 1 solenoid is on, and disables when 0 are on."""
        # Step 0: all off
        tav.set_solenoids(False, False, False)
        self.assertFalse(tav.fan_state)

        # 1 solenoid on -> fan ON
        tav.set_solenoids(True, False, False)
        self.assertTrue(tav.fan_state)

        # 2 solenoids on -> fan ON
        tav.set_solenoids(True, True, False)
        self.assertTrue(tav.fan_state)

        # 3 solenoids on -> fan ON
        tav.set_solenoids(True, True, True)
        self.assertTrue(tav.fan_state)

        # Step down to 2 -> fan still ON
        tav.set_solenoids(True, True, False)
        self.assertTrue(tav.fan_state)

        # Step down to 1 -> fan still ON
        tav.set_solenoids(True, False, False)
        self.assertTrue(tav.fan_state)

        # Step down to 0 -> fan OFF
        tav.set_solenoids(False, False, False)
        self.assertFalse(tav.fan_state)

    def test_rpi_gpio_setup_and_outputs(self):
        """Test that in RPi mode, only pins 10, 9, 11, 14 are setup, signaled, and cleaned up."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0
        mock_gpio.OUT = "OUT"
        mock_gpio.BCM = "BCM"

        with patch.object(tav, "GPIO", mock_gpio), \
             patch.object(tav, "isRpiPresent", True), \
             patch("time.time", side_effect=[0.0, 1.0] * 10), \
             patch("time.sleep", return_value=None):

            tav.run_aerator_test(step_duration=0.1)

            # Setup called strictly for the 4 pins
            setup_pins = [call[0][0] for call in mock_gpio.setup.call_args_list]
            self.assertEqual(setup_pins, [10, 9, 11, 14])

            # Solenoids and fan driven HIGH when active
            mock_gpio.output.assert_any_call(10, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(9, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(11, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(14, mock_gpio.HIGH)

            # Solenoids and fan driven LOW on turn off
            mock_gpio.output.assert_any_call(10, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(9, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(11, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(14, mock_gpio.LOW)

            # Cleanup strictly on the 4 pins
            mock_gpio.cleanup.assert_called_once_with([10, 9, 11, 14])


if __name__ == "__main__":
    unittest.main()
