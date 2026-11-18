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
        piUtils.reset_temperature_control_state()
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
        """Test that mixing tank to main tank valve is removed entirely from hardware (mainValvePin is None)."""
        self.assertIsNone(piUtils.mainValvePin)

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
        """Test applyTemperatureControl opens cooling valve and main valve when tank temperature is HIGH (3 consecutive readings)."""
        self.assertFalse(piUtils.mainValveOpen)
        for _ in range(3):
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
        """Test applyTemperatureControl opens heating valve and main valve when tank temperature is LOW (3 consecutive readings)."""
        self.assertFalse(piUtils.mainValveOpen)
        for _ in range(3):
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
        # 1. Trigger heating (temp < desired, 3 consecutive readings)
        for _ in range(3):
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

        # 3. Trigger cooling (temp > desired, 3 consecutive readings)
        for _ in range(3):
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
        """Test that main valve state updates in software and does NOT drive GPIO pin 24 (which is now cooling dir)."""
        mock_gpio = MagicMock()
        with patch.object(piUtils, "GPIO", mock_gpio), patch.object(piUtils, "isRpiPresent", True):
            piUtils.setMainValve(True)
            self.assertTrue(piUtils.mainValveOpen)

            piUtils.setMainValve(False)
            self.assertFalse(piUtils.mainValveOpen)
            # No GPIO writes should have been made for main valve
            self.assertEqual(len(mock_gpio.output.call_args_list), 0)

    def test_cleanup_closes_main_valve(self):
        """Test cleanupGpio closes the main valve state without outputting to removed main valve pin."""
        piUtils.mainValve(True)
        self.assertTrue(piUtils.mainValveOpen)

        mock_gpio = MagicMock()
        with patch.object(piUtils, "GPIO", mock_gpio), patch.object(piUtils, "isRpiPresent", True):
            piUtils.cleanupGpio()
            self.assertFalse(piUtils.mainValveOpen)
            # Verify heating system relay turned off (26, LOW)
            mock_gpio.output.assert_any_call(26, mock_gpio.LOW)

    def test_rpi_stepper_motor_valves_control(self):
        """Test RPi GPIO output for heating and cooling stepper valves (182 steps, CCW=open, CW=close)."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        piUtils.coolingValveOpen = False
        piUtils.heatingValveOpen = False

        with patch.object(piUtils, "GPIO", mock_gpio), \
             patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "CW", 1), \
             patch.object(piUtils, "CCW", 0), \
             patch("time.sleep", return_value=None):

            # 1. Open Cooling Valve (CCW = 0, 182 pulses on pul=23, enable=25, dir=24)
            piUtils.setRpiCoolingValve(True)
            self.assertTrue(piUtils.coolingValveOpen)
            mock_gpio.output.assert_any_call(25, 0)   # enable LOW
            mock_gpio.output.assert_any_call(24, 0)   # dir CCW
            pul_23_calls = [c for c in mock_gpio.output.call_args_list if c == unittest.mock.call(23, 1)]
            self.assertEqual(len(pul_23_calls), 182)

            # 2. Close Cooling Valve (CW = 1, 182 pulses on pul=23)
            mock_gpio.reset_mock()
            piUtils.setRpiCoolingValve(False)
            self.assertFalse(piUtils.coolingValveOpen)
            mock_gpio.output.assert_any_call(25, 0)   # enable LOW
            mock_gpio.output.assert_any_call(24, 1)   # dir CW
            pul_23_close = [c for c in mock_gpio.output.call_args_list if c == unittest.mock.call(23, 1)]
            self.assertEqual(len(pul_23_close), 182)

            # 3. Open Heating Valve (CCW = 0, 182 pulses on pul=19, enable=6, dir=13)
            mock_gpio.reset_mock()
            piUtils.setRpiHeatingValve(True)
            self.assertTrue(piUtils.heatingValveOpen)
            mock_gpio.output.assert_any_call(6, 0)    # enable LOW
            mock_gpio.output.assert_any_call(13, 0)   # dir CCW
            pul_19_calls = [c for c in mock_gpio.output.call_args_list if c == unittest.mock.call(19, 1)]
            self.assertEqual(len(pul_19_calls), 182)

            # 4. Close Heating Valve (CW = 1, 182 pulses on pul=19)
            mock_gpio.reset_mock()
            piUtils.setRpiHeatingValve(False)
            self.assertFalse(piUtils.heatingValveOpen)
            mock_gpio.output.assert_any_call(6, 0)    # enable LOW
            mock_gpio.output.assert_any_call(13, 1)   # dir CW
            pul_19_close = [c for c in mock_gpio.output.call_args_list if c == unittest.mock.call(19, 1)]
            self.assertEqual(len(pul_19_close), 182)

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
