import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.api.piUtils as piUtils


class TestPeltierCoolingFans(unittest.TestCase):
    def setUp(self):
        piUtils.coolingSystem(False)
        piUtils.coolingSystemActive = False

    def tearDown(self):
        piUtils.coolingSystem(False)
        piUtils.coolingSystemActive = False

    def test_fan_pin_assignments(self):
        """Verify cooling fans are configured on GPIO 1 (fan1) and GPIO 15 (fan2)."""
        self.assertEqual(piUtils.fan1Pin, 1)
        self.assertEqual(piUtils.fan2Pin, 15)

    def test_peltier_element_pin_assignments(self):
        """Verify peltier element pins (12, 16, 20, 21)."""
        self.assertEqual(piUtils.pel1Pin, 12)
        self.assertEqual(piUtils.pel2Pin, 16)
        self.assertEqual(piUtils.pel3Pin, 20)
        self.assertEqual(piUtils.pel4Pin, 21)

    def test_rpi_cooling_system_activates_both_fans(self):
        """When peltier cooling turns ON, both fans on GPIO 1 and GPIO 15 must activate together."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio):

            piUtils.setRpiCoolingSystem(True)
            self.assertTrue(piUtils.coolingSystemActive)

            # Both cooling fans must be turned ON (active-low 0 / GPIO.LOW)
            mock_gpio.output.assert_any_call(piUtils.fan1Pin, 0)
            mock_gpio.output.assert_any_call(piUtils.fan2Pin, 0)

            # All 4 peltier elements must be turned ON
            mock_gpio.output.assert_any_call(piUtils.pel1Pin, 0)
            mock_gpio.output.assert_any_call(piUtils.pel2Pin, 0)
            mock_gpio.output.assert_any_call(piUtils.pel3Pin, 0)
            mock_gpio.output.assert_any_call(piUtils.pel4Pin, 0)

    def test_rpi_cooling_system_deactivates_both_fans(self):
        """When peltier cooling turns OFF, both fans on GPIO 1 and GPIO 15 must turn off together."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio):

            piUtils.setRpiCoolingSystem(False)
            self.assertFalse(piUtils.coolingSystemActive)

            # Both cooling fans must be turned OFF (inactive-high 1 / GPIO.HIGH)
            mock_gpio.output.assert_any_call(piUtils.fan1Pin, 1)
            mock_gpio.output.assert_any_call(piUtils.fan2Pin, 1)

            # All 4 peltier elements must be turned OFF
            mock_gpio.output.assert_any_call(piUtils.pel1Pin, 1)
            mock_gpio.output.assert_any_call(piUtils.pel2Pin, 1)
            mock_gpio.output.assert_any_call(piUtils.pel3Pin, 1)
            mock_gpio.output.assert_any_call(piUtils.pel4Pin, 1)

    def test_peltier_and_fans_initialized_to_off_high(self):
        """Verify peltier elements and fans are configured to initialize to GPIO.HIGH (inactive/OFF)."""
        import inspect
        source = inspect.getsource(piUtils)
        self.assertIn("GPIO.setup(pel1Pin, GPIO.OUT, initial=GPIO.HIGH)", source)
        self.assertIn("GPIO.setup(pel2Pin, GPIO.OUT, initial=GPIO.HIGH)", source)
        self.assertIn("GPIO.setup(pel3Pin, GPIO.OUT, initial=GPIO.HIGH)", source)
        self.assertIn("GPIO.setup(pel4Pin, GPIO.OUT, initial=GPIO.HIGH)", source)
        self.assertIn("GPIO.setup(fan1Pin, GPIO.OUT, initial=GPIO.HIGH)", source)
        self.assertIn("GPIO.setup(fan2Pin, GPIO.OUT, initial=GPIO.HIGH)", source)
        self.assertFalse(piUtils.coolingSystemActive)

    def test_cleanup_gpio_turns_off_cooling_system_and_fans(self):
        """cleanupGpio ensures both fans (GPIO 1 and 15) and peltiers are turned off on shutdown."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        piUtils.coolingSystemActive = True

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio):

            piUtils.cleanupGpio()
            self.assertFalse(piUtils.coolingSystemActive)

            mock_gpio.output.assert_any_call(piUtils.fan1Pin, 1)
            mock_gpio.output.assert_any_call(piUtils.fan2Pin, 1)
            mock_gpio.output.assert_any_call(piUtils.pel1Pin, 1)
            mock_gpio.output.assert_any_call(piUtils.pel2Pin, 1)
            mock_gpio.output.assert_any_call(piUtils.pel3Pin, 1)
            mock_gpio.output.assert_any_call(piUtils.pel4Pin, 1)


if __name__ == "__main__":
    unittest.main()
