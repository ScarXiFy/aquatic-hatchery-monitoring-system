import unittest
from unittest.mock import MagicMock, patch

import app.api.piUtils as piUtils
from app import create_app


class TestMaintenanceModeBackend(unittest.TestCase):
    def setUp(self):
        # Ensure clean normal-mode state before each test
        if piUtils.is_maintenance_mode():
            piUtils.exit_maintenance_mode()
        piUtils.cancel_pump_timers()
        piUtils.reset_temperature_control_state()

        # Establish a known non-trivial normal-mode state
        piUtils.heaterActive = True
        piUtils.heatingSystemActive = False
        piUtils.heatingValveOpen = False
        piUtils.coolingValveOpen = True
        piUtils.coolingSystemActive = True
        piUtils._dosvcounter = 1  # Valve 1 OPEN, Valve 2 CLOSED
        piUtils.doSolenoidValveOpen = True
        piUtils.aeratorFanActive = True
        piUtils.aeratorCoolerFanActive = True
        piUtils.heatingPumpActive = False
        piUtils.coolingPumpActive = True
        piUtils.mixerActive = True
        piUtils.mixingFanActive = True
        piUtils.last_temp_system = "cooling"

    def tearDown(self):
        if piUtils.is_maintenance_mode():
            piUtils.exit_maintenance_mode()
        piUtils.cancel_pump_timers()

    def test_component_control_rejected_outside_maintenance_mode(self):
        self.assertFalse(piUtils.is_maintenance_mode())
        with self.assertRaises(RuntimeError):
            piUtils.set_maintenance_component("heater", False)

    def test_enter_toggle_all_components_and_revert_on_exit(self):
        state = piUtils.enter_maintenance_mode()
        self.assertTrue(state["active"])
        self.assertEqual(
            state["components"],
            {
                "heater": True,
                "heating_valve": False,
                "cooling_valve": True,
                "peltiers": True,
                "aerator_valve_1": True,
                "aerator_valve_2": False,
                "heating_pump": False,
                "cooling_pump": True,
                "mixing_fan": True,
            },
        )

        # Flip every component to the opposite state in maintenance mode
        piUtils.set_maintenance_component("heater", False)
        piUtils.set_maintenance_component("heating_valve", True)
        piUtils.set_maintenance_component("cooling_valve", False)
        piUtils.set_maintenance_component("peltiers", False)
        piUtils.set_maintenance_component("aerator_valve_1", False)
        piUtils.set_maintenance_component("aerator_valve_2", True)
        piUtils.set_maintenance_component("heating_pump", True)
        piUtils.set_maintenance_component("cooling_pump", False)
        piUtils.set_maintenance_component("mixing_fan", False)

        # Verify no pump timers were scheduled by opening valves in maintenance mode
        self.assertIsNone(piUtils._heating_pump_start_timer)
        self.assertIsNone(piUtils._cooling_pump_start_timer)

        mid_state = piUtils.get_maintenance_state()
        self.assertEqual(
            mid_state["components"],
            {
                "heater": False,
                "heating_valve": True,
                "cooling_valve": False,
                "peltiers": False,
                "aerator_valve_1": False,
                "aerator_valve_2": True,
                "heating_pump": True,
                "cooling_pump": False,
                "mixing_fan": False,
            },
        )

        # Test "both" aerator solenoid valves open and close
        state_both_open = piUtils.set_maintenance_component("aerator_valves_both", True)
        self.assertTrue(state_both_open["components"]["aerator_valve_1"])
        self.assertTrue(state_both_open["components"]["aerator_valve_2"])

        state_both_closed = piUtils.set_maintenance_component("aerator_valves_both", False)
        self.assertFalse(state_both_closed["components"]["aerator_valve_1"])
        self.assertFalse(state_both_closed["components"]["aerator_valve_2"])

        # Re-entering maintenance mode must be idempotent and NOT overwrite the original snapshot
        piUtils.enter_maintenance_mode()

        # Exiting maintenance mode must revert EVERY component back to pre-maintenance state
        reverted = piUtils.exit_maintenance_mode()
        self.assertFalse(reverted["active"])
        self.assertEqual(
            reverted["components"],
            {
                "heater": True,
                "heating_valve": False,
                "cooling_valve": True,
                "peltiers": True,
                "aerator_valve_1": True,
                "aerator_valve_2": False,
                "heating_pump": False,
                "cooling_pump": True,
                "mixing_fan": True,
            },
        )
        self.assertEqual(piUtils._dosvcounter, 1)
        self.assertTrue(piUtils.heaterActive)
        self.assertFalse(piUtils.heatingValveOpen)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertFalse(piUtils.heatingPumpActive)
        self.assertTrue(piUtils.coolingPumpActive)
        self.assertTrue(piUtils.mixerActive)

    def test_automated_controls_are_paused_during_maintenance_mode(self):
        piUtils.enter_maintenance_mode()
        piUtils.set_maintenance_component("heater", False)
        piUtils.set_maintenance_component("peltiers", False)
        piUtils.set_maintenance_component("aerator_valves_both", False)

        # Calling automated controls while in maintenance mode must be a complete no-op
        for _ in range(5):
            piUtils.applyTemperatureControl(35.0, 26.0, secondary_temp=30.0)
            piUtils.applyHeatingTankControl(20.0, 40.0)
            piUtils.applyDissolvedOxygenControl(2.0, 7.2)

        state = piUtils.get_maintenance_state()
        self.assertFalse(state["components"]["heater"])
        self.assertFalse(state["components"]["peltiers"])
        self.assertFalse(state["components"]["aerator_valve_1"])
        self.assertFalse(state["components"]["aerator_valve_2"])

    def test_rpi_gpio_actuation_and_reversion(self):
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, "isRpiPresent", True), \
             patch.object(piUtils, "GPIO", mock_gpio), \
             patch.object(piUtils, "_step_valve_motor") as mock_step:
            piUtils.enter_maintenance_mode()
            mock_gpio.output.reset_mock()
            mock_step.reset_mock()

            # Heater OFF (Active-HIGH -> LOW)
            piUtils.set_maintenance_component("heater", False)
            mock_gpio.output.assert_any_call(piUtils.heatingSystemPin, mock_gpio.LOW)

            # Peltiers OFF (all 8 pins Active-LOW -> HIGH)
            piUtils.set_maintenance_component("peltiers", False)
            for pin in piUtils.PELTIER_PINS:
                mock_gpio.output.assert_any_call(pin, mock_gpio.HIGH)

            # Aerator valve 2 OPEN (Active-LOW -> LOW), valve 1 CLOSED (HIGH)
            piUtils.set_maintenance_component("aerator_valve_1", False)
            piUtils.set_maintenance_component("aerator_valve_2", True)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve1, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve2, mock_gpio.LOW)

            # Heating pump ON (Active-LOW -> LOW), cooling pump OFF (HIGH), mixing fan OFF (HIGH)
            piUtils.set_maintenance_component("heating_pump", True)
            piUtils.set_maintenance_component("cooling_pump", False)
            piUtils.set_maintenance_component("mixing_fan", False)
            mock_gpio.output.assert_any_call(piUtils.heatingPumpPin, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.coolingPumpPin, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(piUtils.mixerPin, mock_gpio.HIGH)

            # Heating valve OPEN (was CLOSED -> steps CCW once; second OPEN is no-op)
            piUtils.set_maintenance_component("heating_valve", True)
            piUtils.set_maintenance_component("heating_valve", True)
            self.assertEqual(mock_step.call_count, 1)
            mock_step.assert_called_with(
                piUtils.heatValveEnablePin,
                piUtils.heatValveDirPin,
                piUtils.heatValvePulPin,
                piUtils.CCW,
                piUtils.VALVE_STEP_AMOUNT,
            )

            # Exit maintenance mode -> steps heating valve back CW, does NOT step cooling valve (never moved),
            # and restores all GPIO outputs to pre-maintenance state
            mock_gpio.output.reset_mock()
            mock_step.reset_mock()
            piUtils.exit_maintenance_mode()

            self.assertEqual(mock_step.call_count, 1)
            mock_step.assert_called_with(
                piUtils.heatValveEnablePin,
                piUtils.heatValveDirPin,
                piUtils.heatValvePulPin,
                piUtils.CW,
                piUtils.VALVE_STEP_AMOUNT,
            )
            mock_gpio.output.assert_any_call(piUtils.heatingSystemPin, mock_gpio.HIGH)
            for pin in piUtils.PELTIER_PINS:
                mock_gpio.output.assert_any_call(pin, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve1, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.doSolenoidValve2, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(piUtils.heatingPumpPin, mock_gpio.HIGH)
            mock_gpio.output.assert_any_call(piUtils.coolingPumpPin, mock_gpio.LOW)
            mock_gpio.output.assert_any_call(piUtils.mixerPin, mock_gpio.LOW)

    def test_unknown_component_raises_value_error(self):
        piUtils.enter_maintenance_mode()
        with self.assertRaises(ValueError):
            piUtils.set_maintenance_component("flux_capacitor", True)


class TestMaintenanceModeAPIAndUI(unittest.TestCase):
    def setUp(self):
        if piUtils.is_maintenance_mode():
            piUtils.exit_maintenance_mode()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        if piUtils.is_maintenance_mode():
            piUtils.exit_maintenance_mode()

    def test_maintenance_api_lifecycle(self):
        # Initial state is inactive
        res = self.client.get("/api/maintenance")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data["active"])
        self.assertIn("components", data)

        # Component control before enabling maintenance mode returns 409
        res_conflict = self.client.post(
            "/api/maintenance/component",
            json={"component": "heater", "state": True},
        )
        self.assertEqual(res_conflict.status_code, 409)

        # Enable maintenance mode
        res_on = self.client.post("/api/maintenance/mode", json={"active": True})
        self.assertEqual(res_on.status_code, 200)
        self.assertTrue(res_on.get_json()["active"])

        # Toggle components via API
        res_heater = self.client.post(
            "/api/maintenance/component",
            json={"component": "heater", "state": True},
        )
        self.assertEqual(res_heater.status_code, 200)
        self.assertTrue(res_heater.get_json()["components"]["heater"])

        res_both = self.client.post(
            "/api/maintenance/component",
            json={"component": "aerator_valves_both", "state": False},
        )
        self.assertEqual(res_both.status_code, 200)
        self.assertFalse(res_both.get_json()["components"]["aerator_valve_1"])
        self.assertFalse(res_both.get_json()["components"]["aerator_valve_2"])

        # Unknown component returns 400
        res_bad = self.client.post(
            "/api/maintenance/component",
            json={"component": "invalid_part", "state": True},
        )
        self.assertEqual(res_bad.status_code, 400)

        # Exit maintenance mode reverts states
        res_off = self.client.post("/api/maintenance/mode", json={"active": False})
        self.assertEqual(res_off.status_code, 200)
        self.assertFalse(res_off.get_json()["active"])

    def test_maintenance_ui_elements_present_on_home(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        expected_tokens = [
            'id="open-maintenance-btn"',
            'id="maintenance-modal-overlay"',
            'id="maintenance-mode-toggle-btn"',
            'data-maint-component="heater"',
            'data-maint-component="heating_valve"',
            'data-maint-component="cooling_valve"',
            'data-maint-component="peltiers"',
            'data-maint-component="heating_pump"',
            'data-maint-component="cooling_pump"',
            'data-maint-component="mixing_fan"',
            'data-maint-component="aerator_valve_1"',
            'data-maint-component="aerator_valve_2"',
            "Normal Mode Active",
            "js/maintenance.js",
        ]
        for token in expected_tokens:
            self.assertIn(token, html, f"Missing expected UI element: {token}")

        self.assertNotIn('id="maintenance-feedback"', html)
        self.assertNotIn('id="maintenance-banner-desc"', html)


if __name__ == "__main__":
    unittest.main()
