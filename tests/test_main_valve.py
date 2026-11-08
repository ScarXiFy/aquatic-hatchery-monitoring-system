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
        piUtils.sourceValveOpen = False
        CONTROL_STATE["valves"]["main"] = False
        CONTROL_STATE["valves"]["source"] = False

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
        """Test that mainValvePin is assigned to GPIO 24."""
        self.assertEqual(piUtils.mainValvePin, 24)

    def test_main_valve_direct_dummy_control(self):
        """Test setting main valve state directly in dummy mode."""
        self.assertFalse(piUtils.mainValveOpen)
        piUtils.mainValve(True)
        self.assertTrue(piUtils.mainValveOpen)
        piUtils.mainValve(False)
        self.assertFalse(piUtils.mainValveOpen)

    def test_main_valve_remains_open_for_constant_circulation(self):
        """Test main valve opens and stays open when cooling/heating systems are enabled and disabled."""
        self.assertFalse(piUtils.mainValveOpen)
        piUtils.coolingSystem(True)
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.mainValveOpen)

        # Disabling cooling should keep main valve OPEN for constant circulation
        piUtils.coolingSystem(False)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.mainValveOpen)

        # Enabling heating
        piUtils.heatingSystem(True)
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.mainValveOpen)

        # Disabling heating should also keep main valve OPEN
        piUtils.heatingSystem(False)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.mainValveOpen)

    def test_apply_temperature_control_high_temp(self):
        """Test applyTemperatureControl opens cooling valve and main valve when tank temperature is HIGH."""
        self.assertFalse(piUtils.mainValveOpen)
        piUtils.applyTemperatureControl(
            temperature=28.0,
            setpoint=26.0,
            tolerance=0.5,
            secondary_temp=28.0,
        )
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertFalse(piUtils.heatingValveOpen)
        self.assertTrue(piUtils.mainValveOpen)

    def test_apply_temperature_control_low_temp(self):
        """Test applyTemperatureControl opens heating valve and main valve when tank temperature is LOW."""
        self.assertFalse(piUtils.mainValveOpen)
        piUtils.applyTemperatureControl(
            temperature=24.0,
            setpoint=26.0,
            tolerance=0.5,
            secondary_temp=24.0,
        )
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertFalse(piUtils.coolingValveOpen)
        self.assertTrue(piUtils.mainValveOpen)

    def test_apply_temperature_control_stable_temp_alternation(self):
        """
        Test applyTemperatureControl maintains constant circulation at stable temperature:
        - If last active was heating -> heating valve OFF, cooling valve ON (flow from cooling).
        - If last active was cooling -> cooling valve OFF, heating valve ON (flow from heating).
        - Main valve is ALWAYS ON.
        - At least one valve is ALWAYS ON.
        """
        # 1. Trigger heating (temp < desired)
        piUtils.applyTemperatureControl(
            temperature=24.0,
            setpoint=26.0,
            tolerance=0.5,
            secondary_temp=24.0,
        )
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertFalse(piUtils.coolingValveOpen)
        self.assertTrue(piUtils.mainValveOpen)
        self.assertEqual(piUtils.last_temp_system, "heating")

        # 2. Temperature stabilizes at setpoint (temp == desired) after heating
        piUtils.applyTemperatureControl(
            temperature=26.0,
            setpoint=26.0,
            tolerance=0.5,
            secondary_temp=26.0,
        )
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertFalse(piUtils.coolingSystemActive)
        # Last on was heating -> heating valve off, cooling valve on
        self.assertFalse(piUtils.heatingValveOpen)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertTrue(piUtils.mainValveOpen)
        # Verify at least one valve is open
        self.assertTrue(piUtils.coolingValveOpen or piUtils.heatingValveOpen)

        # 3. Trigger cooling (temp > desired)
        piUtils.applyTemperatureControl(
            temperature=28.0,
            setpoint=26.0,
            tolerance=0.5,
            secondary_temp=28.0,
        )
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertFalse(piUtils.heatingValveOpen)
        self.assertTrue(piUtils.mainValveOpen)
        self.assertEqual(piUtils.last_temp_system, "cooling")

        # 4. Temperature stabilizes at setpoint (temp == desired) after cooling
        piUtils.applyTemperatureControl(
            temperature=26.0,
            setpoint=26.0,
            tolerance=0.5,
            secondary_temp=26.0,
        )
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertFalse(piUtils.coolingSystemActive)
        # Last on was cooling -> cooling valve off, heating valve on
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertFalse(piUtils.coolingValveOpen)
        self.assertTrue(piUtils.mainValveOpen)
        # Verify at least one valve is open
        self.assertTrue(piUtils.coolingValveOpen or piUtils.heatingValveOpen)

    def test_rpi_main_valve_control(self):
        """Test RPi GPIO output when setting main valve with inverted active-low logic."""
        mock_gpio = MagicMock()
        with patch.object(piUtils, "GPIO", mock_gpio), patch.object(piUtils, "isRpiPresent", True):
            # Test opening main valve (active-low -> LOW)
            piUtils.setMainValve(True)
            self.assertTrue(piUtils.mainValveOpen)
            mock_gpio.output.assert_called_with(24, mock_gpio.LOW)

            # Test closing main valve (active-low -> HIGH)
            piUtils.setMainValve(False)
            self.assertFalse(piUtils.mainValveOpen)
            mock_gpio.output.assert_called_with(24, mock_gpio.HIGH)

    def test_cleanup_closes_main_valve(self):
        """Test cleanupGpio closes the main valve and sets GPIO 24 HIGH on RPi."""
        piUtils.mainValve(True)
        self.assertTrue(piUtils.mainValveOpen)

        mock_gpio = MagicMock()
        with patch.object(piUtils, "GPIO", mock_gpio), patch.object(piUtils, "isRpiPresent", True):
            piUtils.cleanupGpio()
            self.assertFalse(piUtils.mainValveOpen)
            mock_gpio.output.assert_any_call(24, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(26, mock_gpio.LOW)

    def test_rpi_water_valves_inverted_logic(self):
        """Test RPi GPIO output for all water valves with reversed logic (active-low: LOW=open/enabled, HIGH=closed/disabled)."""
        mock_gpio = MagicMock()
        with patch.object(piUtils, "GPIO", mock_gpio), patch.object(piUtils, "isRpiPresent", True):
            # Source Valve
            piUtils.setRpiSourceValveState(True)
            self.assertTrue(piUtils.sourceValveOpen)
            mock_gpio.output.assert_called_with(piUtils.sourceValvePin, mock_gpio.LOW)

            piUtils.setRpiSourceValveState(False)
            self.assertFalse(piUtils.sourceValveOpen)
            mock_gpio.output.assert_called_with(piUtils.sourceValvePin, mock_gpio.HIGH)

            # Cooling Valve
            piUtils.setRpiCoolingValve(True)
            self.assertTrue(piUtils.coolingValveOpen)
            mock_gpio.output.assert_called_with(piUtils.coolingValvePin, mock_gpio.LOW)

            piUtils.setRpiCoolingValve(False)
            self.assertFalse(piUtils.coolingValveOpen)
            mock_gpio.output.assert_called_with(piUtils.coolingValvePin, mock_gpio.HIGH)

            # Heating Valve
            piUtils.setRpiHeatingValve(True)
            self.assertTrue(piUtils.heatingValveOpen)
            mock_gpio.output.assert_called_with(piUtils.heatingValvePin, mock_gpio.LOW)

            piUtils.setRpiHeatingValve(False)
            self.assertFalse(piUtils.heatingValveOpen)
            mock_gpio.output.assert_called_with(piUtils.heatingValvePin, mock_gpio.HIGH)

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

    def test_api_route_controls_source_valve(self):
        """Test POST /api/controls/valves/source endpoint opens and closes source valve."""
        # Open source valve via API
        response = self.client.post("/api/controls/valves/source", json={"open": True})
        self.assertEqual(response.status_code, 204)
        self.assertTrue(piUtils.sourceValveOpen)
        self.assertTrue(CONTROL_STATE["valves"]["source"])

        # Close source valve via API
        response = self.client.post("/api/controls/valves/source", json={"open": False})
        self.assertEqual(response.status_code, 204)
        self.assertFalse(piUtils.sourceValveOpen)
        self.assertFalse(CONTROL_STATE["valves"]["source"])


if __name__ == "__main__":
    unittest.main()
