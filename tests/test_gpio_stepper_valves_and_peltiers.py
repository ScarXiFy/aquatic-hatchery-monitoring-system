import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.api.piUtils as piUtils


class TestGpioStepperValvesAndPeltiers(unittest.TestCase):
    def setUp(self):
        piUtils.coolingSystem(False)
        piUtils.coolingSystemActive = False
        piUtils.heatingSystem(False)
        piUtils.heatingSystemActive = False
        piUtils.coolingValveOpen = False
        piUtils.heatingValveOpen = False

    def tearDown(self):
        piUtils.coolingSystem(False)
        piUtils.heatingSystem(False)
        piUtils.coolingValveOpen = False
        piUtils.heatingValveOpen = False

    def test_pin_assignments(self):
        """
        Verify exact GPIO pin assignments:
        - Heating valve stepper: enable=6, dir=13, pul=19
        - Cooling valve stepper: enable=25, dir=24, pul=23
        - 8 peltiers: 14, 15, 7, 1, 12, 16, 20, 21
        - Fans removed from GPIO except mixing (mixerPin=5)
        - Mixing tank to main tank valve removed entirely (mainValvePin is None)
        """
        # Heating stepper motor valve pins
        self.assertEqual(piUtils.heatValveEnablePin, 6)
        self.assertEqual(piUtils.heatValveDirPin, 13)
        self.assertEqual(piUtils.heatValvePulPin, 19)

        # Cooling stepper motor valve pins
        self.assertEqual(piUtils.coolValveEnablePin, 25)
        self.assertEqual(piUtils.coolValveDirPin, 24)
        self.assertEqual(piUtils.coolValvePulPin, 23)

        # Stepper configuration: 178 steps
        self.assertEqual(piUtils.VALVE_STEP_AMOUNT, 178)
        self.assertEqual(piUtils.VALVE_STEPS, 178)

        # Peltier pins: all 8
        expected_peltiers = {14, 15, 7, 1, 12, 16, 20, 21}
        self.assertEqual(set(piUtils.PELTIER_PINS), expected_peltiers)
        self.assertEqual(len(piUtils.PELTIER_PINS), 8)

        # Fans removed from GPIO except mixing
        self.assertIsNone(piUtils.fan1Pin)
        self.assertIsNone(piUtils.fan2Pin)
        self.assertIsNone(piUtils.aeratorFanPin)
        self.assertEqual(piUtils.mixerPin, 5)

        # Water source valve pin maintained on GPIO 8, mixing-to-main valve removed entirely
        self.assertIsNone(piUtils.mainValvePin)
        self.assertEqual(piUtils.sourceValvePin, 8)

    def test_rpi_source_valve_gpio_control(self):
        """Verify source valve opens (LOW) and closes (HIGH) on GPIO 8."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio):

            # Open source valve
            piUtils.setRpiSourceValveState(True)
            self.assertTrue(piUtils.sourceValveOpen)
            mock_gpio.output.assert_called_with(8, 0)

            # Close source valve
            piUtils.setRpiSourceValveState(False)
            self.assertFalse(piUtils.sourceValveOpen)
            mock_gpio.output.assert_called_with(8, 1)

    def test_cooling_peltiers_simultaneous_enable(self):
        """Verify all 8 peltier modules enable simultaneously when cooling is needed."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio):

            # Enable cooling
            piUtils.setRpiCoolingSystem(True)
            self.assertTrue(piUtils.coolingSystemActive)

            # All 8 peltier pins must be driven LOW simultaneously (active-low relay)
            for pin in [14, 15, 7, 1, 12, 16, 20, 21]:
                mock_gpio.output.assert_any_call(pin, 0)

            # Disable cooling
            mock_gpio.reset_mock()
            piUtils.setRpiCoolingSystem(False)
            self.assertFalse(piUtils.coolingSystemActive)

            # All 8 peltier pins must be driven HIGH simultaneously
            for pin in [14, 15, 7, 1, 12, 16, 20, 21]:
                mock_gpio.output.assert_any_call(pin, 1)

    def test_cooling_valve_stepper_open_ccw_and_close_cw(self):
        """
        Verify cooling stepper motor valve:
        - Opens with 178 steps CCW (counterclockwise)
        - Closes with 178 steps CW (clockwise)
        """
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        piUtils.coolingValveOpen = False

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio), \
             patch.object(piUtils, "CW", 1), \
             patch.object(piUtils, "CCW", 0), \
             patch("time.sleep", return_value=None):

            # Open cooling valve (CCW, 178 steps)
            piUtils.setRpiCoolingValve(True)
            self.assertTrue(piUtils.coolingValveOpen)
            mock_gpio.output.assert_any_call(25, 0)  # enable pin LOW
            mock_gpio.output.assert_any_call(24, 0)  # dir pin CCW
            pulses_open = [c for c in mock_gpio.output.call_args_list if c == unittest.mock.call(23, 1)]
            self.assertEqual(len(pulses_open), 178)

            # Redundant open call should not step motor again
            mock_gpio.reset_mock()
            piUtils.setRpiCoolingValve(True)
            self.assertEqual(len(mock_gpio.output.call_args_list), 0)

            # Close cooling valve (CW, 178 steps)
            mock_gpio.reset_mock()
            piUtils.setRpiCoolingValve(False)
            self.assertFalse(piUtils.coolingValveOpen)
            mock_gpio.output.assert_any_call(25, 0)  # enable pin LOW
            mock_gpio.output.assert_any_call(24, 1)  # dir pin CW
            pulses_close = [c for c in mock_gpio.output.call_args_list if c == unittest.mock.call(23, 1)]
            self.assertEqual(len(pulses_close), 178)

            # Redundant close call should not step motor again
            mock_gpio.reset_mock()
            piUtils.setRpiCoolingValve(False)
            self.assertEqual(len(mock_gpio.output.call_args_list), 0)

    def test_heating_valve_stepper_open_ccw_and_close_cw(self):
        """
        Verify heating stepper motor valve:
        - Opens with 178 steps CCW (counterclockwise)
        - Closes with 178 steps CW (clockwise)
        """
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        piUtils.heatingValveOpen = False

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio), \
             patch.object(piUtils, "CW", 1), \
             patch.object(piUtils, "CCW", 0), \
             patch("time.sleep", return_value=None):

            # Open heating valve (CCW, 178 steps)
            piUtils.setRpiHeatingValve(True)
            self.assertTrue(piUtils.heatingValveOpen)
            mock_gpio.output.assert_any_call(6, 0)   # enable pin LOW
            mock_gpio.output.assert_any_call(13, 0)  # dir pin CCW
            pulses_open = [c for c in mock_gpio.output.call_args_list if c == unittest.mock.call(19, 1)]
            self.assertEqual(len(pulses_open), 178)

            # Redundant open call should not step motor again
            mock_gpio.reset_mock()
            piUtils.setRpiHeatingValve(True)
            self.assertEqual(len(mock_gpio.output.call_args_list), 0)

            # Close heating valve (CW, 178 steps)
            mock_gpio.reset_mock()
            piUtils.setRpiHeatingValve(False)
            self.assertFalse(piUtils.heatingValveOpen)
            mock_gpio.output.assert_any_call(6, 0)   # enable pin LOW
            mock_gpio.output.assert_any_call(13, 1)  # dir pin CW
            pulses_close = [c for c in mock_gpio.output.call_args_list if c == unittest.mock.call(19, 1)]
            self.assertEqual(len(pulses_close), 178)

            # Redundant close call should not step motor again
            mock_gpio.reset_mock()
            piUtils.setRpiHeatingValve(False)
            self.assertEqual(len(mock_gpio.output.call_args_list), 0)

    def test_cleanup_gpio_disables_steppers_and_peltiers(self):
        """Verify cleanupGpio disables both stepper motor drivers and all 8 peltiers."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio):

            piUtils.cleanupGpio()

            # Source valve closed (set HIGH)
            mock_gpio.output.assert_any_call(8, 1)

            # Stepper driver enables set HIGH (disabled)
            mock_gpio.output.assert_any_call(6, 1)   # heating valve driver disabled
            mock_gpio.output.assert_any_call(25, 1)  # cooling valve driver disabled
            mock_gpio.output.assert_any_call(22, 1)  # bleed valve driver disabled

            # All 8 peltiers turned off (set HIGH)
            for pin in [14, 15, 7, 1, 12, 16, 20, 21]:
                mock_gpio.output.assert_any_call(pin, 1)

            # GPIO cleaned up
            mock_gpio.cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
