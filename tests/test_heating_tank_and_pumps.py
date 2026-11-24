import unittest
from unittest.mock import MagicMock, patch
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
import app.api.piUtils as piUtils
from app.services.temp_sensor import read_heating_tank_temp


class TestHeatingTankAndPumps(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        piUtils.cancel_pump_timers()
        piUtils.reset_temperature_control_state()
        piUtils.coolingSystem(False)
        piUtils.heatingSystem(False)
        piUtils.mixer(False)
        piUtils._set_heater_relay(False)
        piUtils.heatingPump(False)
        piUtils.coolingPump(False)
        piUtils.heatingValveOpen = False
        piUtils.coolingValveOpen = False
        piUtils.heatingTankSetpoint = 40.0

        if piUtils._mixer_timer is not None:
            piUtils._mixer_timer.cancel()
            piUtils._mixer_timer = None

    def tearDown(self):
        piUtils.cancel_pump_timers()
        piUtils.reset_temperature_control_state()
        piUtils.coolingSystem(False)
        piUtils.heatingSystem(False)
        piUtils.mixer(False)
        piUtils._set_heater_relay(False)
        piUtils.heatingPump(False)
        piUtils.coolingPump(False)
        piUtils.heatingValveOpen = False
        piUtils.coolingValveOpen = False

        if piUtils._mixer_timer is not None:
            piUtils._mixer_timer.cancel()
            piUtils._mixer_timer = None

        self.app_context.pop()

    def wait_for(self, condition, timeout=1.0, interval=0.01):
        start = time.time()
        while time.time() - start < timeout:
            if condition():
                return True
            time.sleep(interval)
        return condition()

    # -------------------------------------------------------------------------
    # 1. PIN ASSIGNMENTS & DEFAULT TIMING CONSTANTS
    # -------------------------------------------------------------------------
    def test_pin_assignments_and_pump_timing_defaults(self):
        """
        Verify updated GPIO pin assignments and default pump timing values:
        - DO solenoid valves reduced to 2: GPIO 10 and GPIO 9 (doSolenoidValve3 is None)
        - Heating tank water pump relay: GPIO 11
        - Cooling tank water pump relay: GPIO 0
        - Pump start delay: 40s (temporary), pump run duration: 120s (2 minutes)
        """
        self.assertEqual(piUtils.DO_SOLENOID_VALVE_COUNT, 2)
        self.assertEqual(piUtils.doSolenoidValve1, 10)
        self.assertEqual(piUtils.doSolenoidValve2, 9)
        self.assertIsNone(piUtils.doSolenoidValve3)

        self.assertEqual(piUtils.heatingPumpPin, 11)
        self.assertEqual(piUtils.coolingPumpPin, 0)

        self.assertEqual(piUtils.HEATING_PUMP_START_DELAY, 40.0)
        self.assertEqual(piUtils.HEATING_PUMP_RUN_DURATION, 120.0)
        self.assertEqual(piUtils.COOLING_PUMP_START_DELAY, 40.0)
        self.assertEqual(piUtils.COOLING_PUMP_RUN_DURATION, 120.0)

    def test_rpi_water_pump_gpio_outputs_and_cleanup(self):
        """Verify heating pump (GPIO 11) and cooling pump (GPIO 0) are Active-LOW relays and reset on cleanup."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio):
            piUtils.setRpiHeatingPump(True)
            self.assertTrue(piUtils.heatingPumpActive)
            mock_gpio.output.assert_called_with(11, 0)

            piUtils.setRpiHeatingPump(False)
            self.assertFalse(piUtils.heatingPumpActive)
            mock_gpio.output.assert_called_with(11, 1)

            piUtils.setRpiCoolingPump(True)
            self.assertTrue(piUtils.coolingPumpActive)
            mock_gpio.output.assert_called_with(0, 0)

            piUtils.setRpiCoolingPump(False)
            self.assertFalse(piUtils.coolingPumpActive)
            mock_gpio.output.assert_called_with(0, 1)

            mock_gpio.reset_mock()
            piUtils.cleanupGpio()
            mock_gpio.output.assert_any_call(11, 1)
            mock_gpio.output.assert_any_call(0, 1)

    # -------------------------------------------------------------------------
    # 2. VALVE-OPEN DELAYED PUMP ACTIVATION & 2-MINUTE DEACTIVATION
    # -------------------------------------------------------------------------
    def test_heating_pump_activates_after_delay_and_deactivates_after_duration(self):
        """
        Opening the heating control valve schedules the heating tank water pump to activate
        after HEATING_PUMP_START_DELAY (40s, shortened for test) and deactivate after
        HEATING_PUMP_RUN_DURATION (120s, shortened for test).
        """
        orig_delay = piUtils.HEATING_PUMP_START_DELAY
        orig_duration = piUtils.HEATING_PUMP_RUN_DURATION
        piUtils.HEATING_PUMP_START_DELAY = 0.08
        piUtils.HEATING_PUMP_RUN_DURATION = 0.12
        try:
            piUtils.heatingValveOpen = False
            piUtils.heatingPump(False)

            # Open heating valve
            piUtils.heatingValve(True)
            self.assertTrue(piUtils.heatingValveOpen)
            # Pump must NOT be active immediately
            self.assertFalse(piUtils.heatingPumpActive)

            # Wait for start delay -> pump activates
            self.assertTrue(self.wait_for(lambda: piUtils.heatingPumpActive, timeout=0.3))

            # Wait for run duration -> pump deactivates
            self.assertTrue(self.wait_for(lambda: not piUtils.heatingPumpActive, timeout=0.4))
        finally:
            piUtils.HEATING_PUMP_START_DELAY = orig_delay
            piUtils.HEATING_PUMP_RUN_DURATION = orig_duration

    def test_cooling_pump_activates_after_delay_and_deactivates_after_duration(self):
        """
        Opening the cooling control valve schedules the cooling tank water pump to activate
        after COOLING_PUMP_START_DELAY (40s, shortened for test) and deactivate after
        COOLING_PUMP_RUN_DURATION (120s, shortened for test).
        """
        orig_delay = piUtils.COOLING_PUMP_START_DELAY
        orig_duration = piUtils.COOLING_PUMP_RUN_DURATION
        piUtils.COOLING_PUMP_START_DELAY = 0.08
        piUtils.COOLING_PUMP_RUN_DURATION = 0.12
        try:
            piUtils.coolingValveOpen = False
            piUtils.coolingPump(False)

            # Open cooling valve
            piUtils.coolingValve(True)
            self.assertTrue(piUtils.coolingValveOpen)
            # Pump must NOT be active immediately
            self.assertFalse(piUtils.coolingPumpActive)

            # Wait for start delay -> pump activates
            self.assertTrue(self.wait_for(lambda: piUtils.coolingPumpActive, timeout=0.3))

            # Wait for run duration -> pump deactivates
            self.assertTrue(self.wait_for(lambda: not piUtils.coolingPumpActive, timeout=0.4))
        finally:
            piUtils.COOLING_PUMP_START_DELAY = orig_delay
            piUtils.COOLING_PUMP_RUN_DURATION = orig_duration

    # -------------------------------------------------------------------------
    # 3. SENSOR-BASED HEATER & SETPOINT-LOWERED COOLDOWN PUMP LOGIC
    # -------------------------------------------------------------------------
    def test_heating_tank_overtemp_after_setpoint_change_activates_pump_until_met(self):
        """
        When the heating tank setpoint has just been set and the heating tank sensor detects
        that the current temperature is too high (> setpoint), the heating tank water pump
        must activate until the setpoint is met (<= setpoint), while the heater stays OFF.
        """
        # User sets desired heating tank temperature to 35C while tank is currently at 45C
        piUtils.set_heating_tank_setpoint(35.0)
        piUtils.applyHeatingTankControl(heating_tank_temp=45.0, setpoint=35.0)

        self.assertFalse(piUtils.heaterActive)
        self.assertTrue(piUtils.heatingPumpActive)
        self.assertTrue(piUtils._heating_pump_cooldown_active)

        # Still too high (38.0C > 35.0C) -> pump remains ON, heater remains OFF
        piUtils.applyHeatingTankControl(heating_tank_temp=38.0, setpoint=35.0)
        self.assertFalse(piUtils.heaterActive)
        self.assertTrue(piUtils.heatingPumpActive)
        self.assertTrue(piUtils._heating_pump_cooldown_active)

        # Setpoint met (35.0C <= 35.0C) -> pump deactivates, heater stays OFF
        piUtils.applyHeatingTankControl(heating_tank_temp=35.0, setpoint=35.0)
        self.assertFalse(piUtils.heaterActive)
        self.assertFalse(piUtils.heatingPumpActive)
        self.assertFalse(piUtils._heating_pump_cooldown_active)

    def test_heating_tank_overshoot_without_recent_setpoint_change_does_not_activate_pump(self):
        """
        If the setpoint was NOT just set, a slight temperature above setpoint simply turns
        the heater OFF without triggering the cooldown water pump.
        """
        piUtils.applyHeatingTankControl(heating_tank_temp=41.0, setpoint=40.0)
        self.assertFalse(piUtils.heaterActive)
        self.assertFalse(piUtils.heatingPumpActive)
        self.assertFalse(piUtils._heating_pump_cooldown_active)

    def test_raising_setpoint_during_cooldown_stops_pump_and_activates_heater(self):
        """
        If the heating tank is cooling down via the water pump and the user raises the
        setpoint above the current temperature, the pump stops and the heater activates.
        """
        piUtils.set_heating_tank_setpoint(35.0)
        piUtils.applyHeatingTankControl(heating_tank_temp=45.0, setpoint=35.0)
        self.assertTrue(piUtils.heatingPumpActive)
        self.assertFalse(piUtils.heaterActive)

        # User raises setpoint to 40C (max) while tank is at 38C
        piUtils.set_heating_tank_setpoint(40.0)
        piUtils.applyHeatingTankControl(heating_tank_temp=38.0, setpoint=40.0)
        self.assertFalse(piUtils.heatingPumpActive)
        self.assertFalse(piUtils._heating_pump_cooldown_active)
        self.assertTrue(piUtils.heaterActive)

    # -------------------------------------------------------------------------
    # 4. WEB APP SLIDER & API INTEGRATION
    # -------------------------------------------------------------------------
    def test_web_app_slider_present_and_api_updates_heating_tank_setpoint(self):
        """Verify home page renders the heating tank slider (27-40C) and API persists & triggers setpoint change."""
        res_html = self.client.get("/")
        self.assertEqual(res_html.status_code, 200)
        html = res_html.get_data(as_text=True)
        self.assertIn('data-control-slider="heating_tank_setpoint"', html)
        self.assertIn('min="27" max="40"', html)
        self.assertIn('id="heating_tank_setpoint-output"', html)

        # GET /api/controls includes heating_tank_setpoint
        res_get = self.client.get("/api/controls")
        self.assertEqual(res_get.status_code, 200)
        data = res_get.get_json()
        self.assertIn("heating_tank_setpoint", data["sliders"])

        # Ensure main tank setpoint is 26.0 first
        self.client.post("/api/controls/sliders", json={"temperature_setpoint": 26.0})

        # POST /api/controls/sliders updates heating_tank_setpoint and marks setpoint as just set
        res_post = self.client.post(
            "/api/controls/sliders",
            json={"heating_tank_setpoint": 34.0},
        )
        self.assertEqual(res_post.status_code, 200)
        post_data = res_post.get_json()
        self.assertEqual(post_data["sliders"]["heating_tank_setpoint"], 34.0)
        self.assertEqual(piUtils.heatingTankSetpoint, 34.0)
        self.assertTrue(piUtils._heating_tank_setpoint_just_set)

        # Now sensor reads 39.0C (> 34.0C) -> heating tank water pump activates
        piUtils.applyHeatingTankControl(heating_tank_temp=39.0, setpoint=34.0)
        self.assertTrue(piUtils.heatingPumpActive)
        self.assertFalse(piUtils.heaterActive)

    def test_heating_tank_setpoint_bounds_and_higher_than_main_tank_rule(self):
        """
        Verify heating_tank_setpoint rules:
        - Minimum allowed is 27C, maximum allowed is 40C.
        - Must always be strictly higher than temperature_setpoint.
        - Raising temperature_setpoint above stored heating_tank_setpoint bumps heating_tank_setpoint.
        """
        # Set main tank to 25C
        res = self.client.post("/api/controls/sliders", json={"temperature_setpoint": 25.0})
        self.assertEqual(res.status_code, 200)

        # Below minimum (26 < 27) -> 400
        res_low = self.client.post("/api/controls/sliders", json={"heating_tank_setpoint": 26.0})
        self.assertEqual(res_low.status_code, 400)

        # Above maximum (41 > 40) -> 400
        res_high = self.client.post("/api/controls/sliders", json={"heating_tank_setpoint": 41.0})
        self.assertEqual(res_high.status_code, 400)

        # Exact boundary 27 and 40 -> 200 (since main tank is 25 < 27)
        self.assertEqual(
            self.client.post("/api/controls/sliders", json={"heating_tank_setpoint": 27.0}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post("/api/controls/sliders", json={"heating_tank_setpoint": 40.0}).status_code,
            200,
        )

        # Set main tank to 30C -> heating tank setpoint <= 30C must be rejected
        self.assertEqual(
            self.client.post("/api/controls/sliders", json={"temperature_setpoint": 30.0}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post("/api/controls/sliders", json={"heating_tank_setpoint": 30.0}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/controls/sliders", json={"heating_tank_setpoint": 28.0}).status_code,
            400,
        )
        # 31C (> 30C) is accepted
        res_ok = self.client.post("/api/controls/sliders", json={"heating_tank_setpoint": 31.0})
        self.assertEqual(res_ok.status_code, 200)
        self.assertEqual(res_ok.get_json()["sliders"]["heating_tank_setpoint"], 31.0)

        # Raising main tank setpoint to 33C automatically bumps heating_tank_setpoint to 34C
        res_bump = self.client.post("/api/controls/sliders", json={"temperature_setpoint": 33.0})
        self.assertEqual(res_bump.status_code, 200)
        self.assertEqual(res_bump.get_json()["sliders"]["heating_tank_setpoint"], 34.0)

    @patch("app.services.temp_sensor.read_ds18b20")
    def test_read_heating_tank_temp_service(self, mock_read):
        mock_read.return_value = 41.25
        self.assertEqual(read_heating_tank_temp(device_id="28-000000abcdef"), 41.25)


if __name__ == "__main__":
    unittest.main()
