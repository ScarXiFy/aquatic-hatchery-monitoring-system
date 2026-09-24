import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
import app.api.piUtils as piUtils
from app.api.routes import CONTROL_STATE


class TestMainValve(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Reset all states
        piUtils.coolingSystem(False)
        piUtils.heatingSystem(False)
        piUtils.mainValve(False)
        piUtils.mixer(False)
        if piUtils._mixer_timer is not None:
            piUtils._mixer_timer.cancel()
            piUtils._mixer_timer = None
        if piUtils._heating_thread is not None and piUtils._heating_thread.is_alive():
            piUtils._heating_stop_event.set()
            piUtils._heating_thread.join(timeout=1.0)
            piUtils._heating_thread = None

        piUtils.coolingSystemActive = False
        piUtils.heatingSystemActive = False
        piUtils.mainValveOpen = False
        CONTROL_STATE["valves"]["main"] = False

    def tearDown(self):
        piUtils.coolingSystem(False)
        piUtils.heatingSystem(False)
        piUtils.mainValve(False)
        piUtils.mixer(False)
        if piUtils._mixer_timer is not None:
            piUtils._mixer_timer.cancel()
            piUtils._mixer_timer = None
        if piUtils._heating_thread is not None and piUtils._heating_thread.is_alive():
            piUtils._heating_stop_event.set()
            piUtils._heating_thread.join(timeout=1.0)
            piUtils._heating_thread = None

        self.app_context.pop()

    def test_pin_assignment(self):
        """Test that mainValvePin is assigned to GPIO 26."""
        self.assertEqual(piUtils.mainValvePin, 26)

    def test_main_valve_direct_dummy_control(self):
        """Test setting main valve state directly in dummy mode."""
        self.assertFalse(piUtils.mainValveOpen)
        piUtils.mainValve(True)
        self.assertTrue(piUtils.mainValveOpen)
        piUtils.mainValve(False)
        self.assertFalse(piUtils.mainValveOpen)

    def test_main_valve_opens_and_closes_with_cooling(self):
        """Test main valve automatically opens when cooling is enabled and closes when disabled."""
        self.assertFalse(piUtils.mainValveOpen)
        piUtils.coolingSystem(True)
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.mainValveOpen)

        piUtils.coolingSystem(False)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertFalse(piUtils.mainValveOpen)

    def test_main_valve_opens_and_closes_with_heating(self):
        """Test main valve automatically opens when heating is enabled and closes when disabled."""
        self.assertFalse(piUtils.mainValveOpen)
        piUtils.heatingSystem(True)
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.mainValveOpen)

        piUtils.heatingSystem(False)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertFalse(piUtils.mainValveOpen)

    def test_main_valve_remains_open_if_other_system_active(self):
        """Test main valve stays open when switching from cooling to heating or vice versa."""
        piUtils.coolingSystem(True)
        self.assertTrue(piUtils.mainValveOpen)

        # Enable heating while cooling is active
        piUtils.heatingSystem(True)
        # Disable cooling - heating is still active
        piUtils.coolingSystem(False)
        self.assertTrue(piUtils.mainValveOpen)

        # Disable heating - now both are off
        piUtils.heatingSystem(False)
        self.assertFalse(piUtils.mainValveOpen)

    def test_apply_temperature_control_high_temp(self):
        """Test applyTemperatureControl opens main valve when tank temperature is HIGH."""
        self.assertFalse(piUtils.mainValveOpen)
        piUtils.applyTemperatureControl(
            temperature=28.0,
            setpoint=26.0,
            tolerance=0.5,
            secondary_temp=28.0,
        )
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertTrue(piUtils.mainValveOpen)

    def test_apply_temperature_control_low_temp(self):
        """Test applyTemperatureControl opens main valve when tank temperature is LOW."""
        self.assertFalse(piUtils.mainValveOpen)
        piUtils.applyTemperatureControl(
            temperature=24.0,
            setpoint=26.0,
            tolerance=0.5,
            secondary_temp=24.0,
        )
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertTrue(piUtils.mainValveOpen)

    def test_apply_temperature_control_stable_temp(self):
        """Test applyTemperatureControl closes main valve when tank temperature is STABLE."""
        # First trigger heating to open the valve
        piUtils.applyTemperatureControl(
            temperature=24.0,
            setpoint=26.0,
            tolerance=0.5,
            secondary_temp=24.0,
        )
        self.assertTrue(piUtils.mainValveOpen)

        # Now supply a stable temperature reading
        piUtils.applyTemperatureControl(
            temperature=26.0,
            setpoint=26.0,
            tolerance=0.5,
            secondary_temp=26.0,
        )
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertFalse(piUtils.coolingValveOpen)
        self.assertFalse(piUtils.heatingValveOpen)
        self.assertFalse(piUtils.mainValveOpen)

    def test_rpi_main_valve_control(self):
        """Test RPi GPIO output when setting main valve."""
        mock_gpio = MagicMock()
        with patch.object(piUtils, "GPIO", mock_gpio), patch.object(piUtils, "isRpiPresent", True):
            # Test opening main valve
            piUtils.setMainValve(True)
            self.assertTrue(piUtils.mainValveOpen)
            mock_gpio.output.assert_called_with(26, mock_gpio.HIGH)

            # Test closing main valve
            piUtils.setMainValve(False)
            self.assertFalse(piUtils.mainValveOpen)
            mock_gpio.output.assert_called_with(26, mock_gpio.LOW)

    def test_cleanup_closes_main_valve(self):
        """Test cleanupGpio closes the main valve and sets GPIO 26 LOW on RPi."""
        piUtils.mainValve(True)
        self.assertTrue(piUtils.mainValveOpen)

        mock_gpio = MagicMock()
        with patch.object(piUtils, "GPIO", mock_gpio), patch.object(piUtils, "isRpiPresent", True):
            piUtils.cleanupGpio()
            self.assertFalse(piUtils.mainValveOpen)
            mock_gpio.output.assert_any_call(26, mock_gpio.LOW)

    def test_api_route_controls_main_valve(self):
        """Test POST /api/controls/valves/main endpoint."""
        # Open valve via API
        response = self.client.post("/api/controls/valves/main", json={"open": True})
        self.assertEqual(response.status_code, 204)
        self.assertTrue(piUtils.mainValveOpen)
        self.assertTrue(CONTROL_STATE["valves"]["main"])

        # Close valve via API
        response = self.client.post("/api/controls/valves/main", json={"open": False})
        self.assertEqual(response.status_code, 204)
        self.assertFalse(piUtils.mainValveOpen)
        self.assertFalse(CONTROL_STATE["valves"]["main"])


if __name__ == "__main__":
    unittest.main()
