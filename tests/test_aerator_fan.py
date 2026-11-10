import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
import app.api.piUtils as piUtils


class TestAeratorCoolerFan(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Reset DO solenoid valves and fan to known state (0 valves open)
        piUtils.setDoSolenoidValveCount(0)

    def tearDown(self):
        piUtils.setDoSolenoidValveCount(0)
        self.app_context.pop()

    def test_pin_assignment(self):
        """Test that aerator cooler fan is configured for GPIO 14."""
        self.assertEqual(piUtils.aeratorFanPin, 14)
        self.assertEqual(piUtils.aeratorCoolerFanPin, 14)

    def test_dummy_fan_enables_when_at_least_one_solenoid_valve_open(self):
        """Test dummy fan state is ON when 1, 2, or 3 solenoid valves are open, and OFF when 0."""
        # 0 valves open -> fan OFF
        piUtils.setDoSolenoidValveCount(0)
        self.assertEqual(piUtils._dosvcounter, 0)
        self.assertFalse(piUtils.aeratorFanActive)
        self.assertFalse(piUtils.aeratorCoolerFanActive)

        # 1 valve open -> fan ON
        piUtils.doSolenoidValve(True)
        self.assertEqual(piUtils._dosvcounter, 1)
        self.assertTrue(piUtils.aeratorFanActive)
        self.assertTrue(piUtils.aeratorCoolerFanActive)

        # 2 valves open -> fan ON
        piUtils.doSolenoidValve(True)
        self.assertEqual(piUtils._dosvcounter, 2)
        self.assertTrue(piUtils.aeratorFanActive)
        self.assertTrue(piUtils.aeratorCoolerFanActive)

        # 3 valves open -> fan ON
        piUtils.doSolenoidValve(True)
        self.assertEqual(piUtils._dosvcounter, 3)
        self.assertTrue(piUtils.aeratorFanActive)
        self.assertTrue(piUtils.aeratorCoolerFanActive)

        # Close 1 valve -> 2 valves open -> fan still ON
        piUtils.doSolenoidValve(False)
        self.assertEqual(piUtils._dosvcounter, 2)
        self.assertTrue(piUtils.aeratorFanActive)

        # Close 1 valve -> 1 valve open -> fan still ON
        piUtils.doSolenoidValve(False)
        self.assertEqual(piUtils._dosvcounter, 1)
        self.assertTrue(piUtils.aeratorFanActive)

        # Close 1 valve -> 0 valves open -> fan turns OFF
        piUtils.doSolenoidValve(False)
        self.assertEqual(piUtils._dosvcounter, 0)
        self.assertFalse(piUtils.aeratorFanActive)
        self.assertFalse(piUtils.aeratorCoolerFanActive)

    def test_direct_set_fan(self):
        """Test direct control of the aerator cooler fan in dummy mode."""
        piUtils.setAeratorFan(True)
        self.assertTrue(piUtils.aeratorFanActive)
        self.assertTrue(piUtils.aeratorCoolerFanActive)

        piUtils.setAeratorFan(False)
        self.assertFalse(piUtils.aeratorFanActive)
        self.assertFalse(piUtils.aeratorCoolerFanActive)

    def test_set_do_solenoid_valve_count_helper(self):
        """Test setting valve counts directly (0-3) and clamping."""
        piUtils.setDoSolenoidValveCount(2)
        self.assertEqual(piUtils._dosvcounter, 2)
        self.assertTrue(piUtils.aeratorFanActive)

        piUtils.setDoSolenoidValveCount(0)
        self.assertEqual(piUtils._dosvcounter, 0)
        self.assertFalse(piUtils.aeratorFanActive)

        # Clamping test
        piUtils.setDoSolenoidValveCount(5)
        self.assertEqual(piUtils._dosvcounter, 3)
        self.assertTrue(piUtils.aeratorFanActive)

        piUtils.setDoSolenoidValveCount(-2)
        self.assertEqual(piUtils._dosvcounter, 0)
        self.assertFalse(piUtils.aeratorFanActive)

    def test_rpi_aerator_fan_gpio_control(self):
        """Test RPi GPIO output for aerator fan on GPIO 14 (Active-LOW: LOW=ON, HIGH=OFF)."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "GPIO", mock_gpio), \
             patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "doSolenoidValve", piUtils.setRpiDoSolenoidValve):
            # Start with 0 valves -> fan OFF (HIGH)
            piUtils.setDoSolenoidValveCount(0)
            mock_gpio.output.assert_called_with(14, mock_gpio.HIGH)
            self.assertFalse(piUtils.aeratorFanActive)

            # Open 1 valve -> GPIO 14 set LOW (ON)
            piUtils.setDoSolenoidValveCount(1)
            mock_gpio.output.assert_called_with(14, mock_gpio.LOW)
            self.assertTrue(piUtils.aeratorFanActive)

            # Open 3 valves -> GPIO 14 remains LOW (ON)
            piUtils.setDoSolenoidValveCount(3)
            mock_gpio.output.assert_called_with(14, mock_gpio.LOW)
            self.assertTrue(piUtils.aeratorFanActive)

            # Close all valves -> GPIO 14 set HIGH (OFF)
            piUtils.setDoSolenoidValveCount(0)
            mock_gpio.output.assert_called_with(14, mock_gpio.HIGH)
            self.assertFalse(piUtils.aeratorFanActive)

    def test_rpi_do_solenoid_valves_gpio_control(self):
        """Test RPi GPIO output for DO solenoid valves (Active-LOW: LOW=OPEN, HIGH=CLOSED)."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "GPIO", mock_gpio), \
             patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "doSolenoidValve", piUtils.setRpiDoSolenoidValve):
            # Count 0: all closed (HIGH, HIGH, HIGH)
            piUtils.setDoSolenoidValveCount(0)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve1, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve2, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve3, mock_gpio.HIGH)

            # Count 1: 1 open, 2 closed (LOW, HIGH, HIGH)
            mock_gpio.reset_mock()
            piUtils.setDoSolenoidValveCount(1)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve1, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve2, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve3, mock_gpio.HIGH)

            # Count 2: 2 open, 1 closed (LOW, LOW, HIGH)
            mock_gpio.reset_mock()
            piUtils.setDoSolenoidValveCount(2)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve1, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve2, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve3, mock_gpio.HIGH)

            # Count 3: all 3 open (LOW, LOW, LOW)
            mock_gpio.reset_mock()
            piUtils.setDoSolenoidValveCount(3)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve1, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve2, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve3, mock_gpio.LOW)

            # Stepping down (closing one by one):
            mock_gpio.reset_mock()
            piUtils.doSolenoidValve(False)  # 3 -> 2
            self.assertEqual(piUtils._dosvcounter, 2)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve1, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve2, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve3, mock_gpio.HIGH)

            mock_gpio.reset_mock()
            piUtils.doSolenoidValve(False)  # 2 -> 1
            self.assertEqual(piUtils._dosvcounter, 1)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve1, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve2, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve3, mock_gpio.HIGH)

            mock_gpio.reset_mock()
            piUtils.doSolenoidValve(False)  # 1 -> 0
            self.assertEqual(piUtils._dosvcounter, 0)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve1, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve2, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve3, mock_gpio.HIGH)

    def test_cleanup_disables_aerator_fan(self):
        """Test cleanupGpio turns off aerator fan on GPIO 14 (Active-LOW: HIGH=OFF)."""
        piUtils.setDoSolenoidValveCount(2)
        self.assertTrue(piUtils.aeratorFanActive)

        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "GPIO", mock_gpio), \
             patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "doSolenoidValve", piUtils.setRpiDoSolenoidValve):
            piUtils.cleanupGpio()
            self.assertFalse(piUtils.aeratorFanActive)
            mock_gpio.output.assert_any_call(14, mock_gpio.HIGH)


if __name__ == "__main__":
    unittest.main()
