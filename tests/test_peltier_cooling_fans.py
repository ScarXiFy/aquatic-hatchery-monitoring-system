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

    def test_peltier_element_pin_assignments(self):
        """Verify all 8 peltier element pins (14, 15, 7, 1, 12, 16, 20, 21)."""
        expected_pins = [12, 16, 20, 21, 14, 15, 7, 1]
        for pin in expected_pins:
            self.assertIn(pin, piUtils.PELTIER_PINS)
        self.assertEqual(len(piUtils.PELTIER_PINS), 8)
        self.assertEqual(piUtils.pel1Pin, 12)
        self.assertEqual(piUtils.pel2Pin, 16)
        self.assertEqual(piUtils.pel3Pin, 20)
        self.assertEqual(piUtils.pel4Pin, 21)
        self.assertEqual(piUtils.pel5Pin, 14)
        self.assertEqual(piUtils.pel6Pin, 15)
        self.assertEqual(piUtils.pel7Pin, 7)
        self.assertEqual(piUtils.pel8Pin, 1)

    def test_fans_removed_from_gpio(self):
        """Verify cooler fans are removed from GPIO (fan1Pin and fan2Pin are None or unmapped)."""
        self.assertIsNone(piUtils.fan1Pin)
        self.assertIsNone(piUtils.fan2Pin)

    def test_rpi_cooling_system_activates_all_8_peltiers_simultaneously(self):
        """When cooling is needed, all 8 peltier elements must enable simultaneously (active-low 0 / GPIO.LOW)."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio):

            piUtils.setRpiCoolingSystem(True)
            self.assertTrue(piUtils.coolingSystemActive)

            # All 8 peltier elements must be turned ON simultaneously
            for pin in piUtils.PELTIER_PINS:
                mock_gpio.output.assert_any_call(pin, 0)

    def test_rpi_cooling_system_deactivates_all_8_peltiers(self):
        """When peltier cooling turns OFF, all 8 peltiers must turn off together (inactive-high 1 / GPIO.HIGH)."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio):

            piUtils.setRpiCoolingSystem(False)
            self.assertFalse(piUtils.coolingSystemActive)

            # All 8 peltier elements must be turned OFF
            for pin in piUtils.PELTIER_PINS:
                mock_gpio.output.assert_any_call(pin, 1)

    def test_peltier_initialized_to_off_high(self):
        """Verify all 8 peltier elements are configured to initialize to GPIO.HIGH (inactive/OFF)."""
        import inspect
        source = inspect.getsource(piUtils)
        self.assertIn("for pin in PELTIER_PINS:", source)
        self.assertIn("GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)", source)
        self.assertFalse(piUtils.coolingSystemActive)

    def test_cleanup_gpio_turns_off_all_8_peltiers(self):
        """cleanupGpio ensures all 8 peltiers are turned off on shutdown."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        piUtils.coolingSystemActive = True

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio):

            piUtils.cleanupGpio()
            self.assertFalse(piUtils.coolingSystemActive)

            for pin in piUtils.PELTIER_PINS:
                mock_gpio.output.assert_any_call(pin, 1)


if __name__ == "__main__":
    unittest.main()
