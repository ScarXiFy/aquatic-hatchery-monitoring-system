import unittest
from unittest.mock import patch, mock_open
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
import app.api.piUtils as piUtils
from app.sensors.simulator import generate_sensor_reading
import app.sensors.simulator as simulator


class TestTempDebounceAndDoControl(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Reset piUtils hardware/dummy states
        piUtils.coolingSystem(False)
        piUtils.heatingSystem(False)
        piUtils.mainValve(False)
        piUtils.mixer(False)
        piUtils.setDoSolenoidValveCount(0)
        piUtils.doBleedValve(0.0)
        piUtils._last_do_trig_time = 0.0
        piUtils.reset_temperature_control_state()

        if piUtils._mixer_timer is not None:
            piUtils._mixer_timer.cancel()
            piUtils._mixer_timer = None
        if piUtils._heating_thread is not None and piUtils._heating_thread.is_alive():
            piUtils._heating_stop_event.set()
            piUtils._heating_thread.join(timeout=1.0)
            piUtils._heating_thread = None

        simulator.current_temp = 26.0
        simulator.current_do = 7.2

    def tearDown(self):
        piUtils.coolingSystem(False)
        piUtils.heatingSystem(False)
        piUtils.mainValve(False)
        piUtils.mixer(False)
        piUtils.setDoSolenoidValveCount(0)
        piUtils.reset_temperature_control_state()

        if piUtils._mixer_timer is not None:
            piUtils._mixer_timer.cancel()
            piUtils._mixer_timer = None
        if piUtils._heating_thread is not None and piUtils._heating_thread.is_alive():
            piUtils._heating_stop_event.set()
            piUtils._heating_thread.join(timeout=1.0)
            piUtils._heating_thread = None

        self.app_context.pop()

    # -------------------------------------------------------------------------
    # 1. DO CONTROL LOGIC TESTS
    # -------------------------------------------------------------------------
    def test_do_control_closes_all_valves_and_fully_opens_bleed_between_setpoint_and_upper_deviation(self):
        """
        When DO is between setpoint (7.2) and setpoint + 0.3 (7.5),
        all DO aerator valves must be closed (count = 0, fan OFF) instead of
        maintaining current open valves, and the bleed valve must fully open (100%).
        """
        # Start with 2 open aerator valves, bleed valve at 33%, and fan ON
        piUtils.setDoSolenoidValveCount(2)
        piUtils.doBleedValve(33.0)
        self.assertEqual(piUtils._dosvcounter, 2)
        self.assertTrue(piUtils.aeratorFanActive)

        # Apply DO control at setpoint (7.2 mg/L)
        piUtils.applyDissolvedOxygenControl(do_level=7.2, setpoint=7.2, tolerance=0.3)
        self.assertEqual(piUtils._dosvcounter, 0)
        self.assertFalse(piUtils.doSolenoidValveOpen)
        self.assertFalse(piUtils.aeratorFanActive)
        self.assertEqual(piUtils.doBleedValvePercent, 100.0)

        # Re-open valves to test upper boundary within 0.3 deviation (7.45 mg/L)
        piUtils.setDoSolenoidValveCount(3)
        piUtils.doBleedValve(0.0)
        self.assertEqual(piUtils._dosvcounter, 3)
        piUtils.applyDissolvedOxygenControl(do_level=7.45, setpoint=7.2, tolerance=0.3)
        self.assertEqual(piUtils._dosvcounter, 0)
        self.assertFalse(piUtils.doSolenoidValveOpen)
        self.assertFalse(piUtils.aeratorFanActive)
        self.assertEqual(piUtils.doBleedValvePercent, 100.0)

        # Exactly at high threshold (7.5 mg/L)
        piUtils.setDoSolenoidValveCount(1)
        piUtils.doBleedValve(66.0)
        piUtils.applyDissolvedOxygenControl(do_level=7.5, setpoint=7.2, tolerance=0.3)
        self.assertEqual(piUtils._dosvcounter, 0)
        self.assertFalse(piUtils.aeratorFanActive)
        self.assertEqual(piUtils.doBleedValvePercent, 100.0)

    def test_do_control_recovers_between_low_and_setpoint(self):
        """
        When DO is recovering (between 6.9 and 7.2 mg/L),
        it should maintain the current number of open valves without closing them.
        """
        piUtils.setDoSolenoidValveCount(2)
        piUtils.applyDissolvedOxygenControl(do_level=7.05, setpoint=7.2, tolerance=0.3)
        self.assertEqual(piUtils._dosvcounter, 2)
        self.assertTrue(piUtils.aeratorFanActive)

    def test_do_control_enabled_when_reaching_0_3_below_setpoint(self):
        """
        When DO drops to or below setpoint - 0.3 (<= 6.9 mg/L),
        DO control is enabled: opens aerator solenoid valve (increments count),
        and closes bleed valve by 33%.
        """
        piUtils.setDoSolenoidValveCount(0)
        piUtils.doBleedValve(66.0)
        piUtils._last_do_trig_time = 0.0

        # DO = 6.9 (exactly 0.3 below setpoint)
        piUtils.applyDissolvedOxygenControl(do_level=6.9, setpoint=7.2, tolerance=0.3)
        self.assertEqual(piUtils._dosvcounter, 1)
        self.assertTrue(piUtils.doSolenoidValveOpen)
        self.assertTrue(piUtils.aeratorFanActive)
        self.assertAlmostEqual(piUtils.doBleedValvePercent, 33.0, places=1)

    def test_do_control_high_do_above_upper_deviation(self):
        """
        When DO > setpoint + 0.3 (> 7.5 mg/L), all aerator valves are closed,
        and bleed valve is fully opened (100.0%).
        """
        piUtils.setDoSolenoidValveCount(2)
        piUtils.doBleedValve(0.0)
        piUtils._last_do_trig_time = 0.0

        piUtils.applyDissolvedOxygenControl(do_level=7.6, setpoint=7.2, tolerance=0.3)
        self.assertEqual(piUtils._dosvcounter, 0)
        self.assertFalse(piUtils.aeratorFanActive)
        self.assertEqual(piUtils.doBleedValvePercent, 100.0)

    # -------------------------------------------------------------------------
    # 2. 85°C SENSOR STARTUP FILTERING TESTS
    # -------------------------------------------------------------------------
    def test_parse_ds18b20_rejects_85c_power_on_reset(self):
        """
        _parse_ds18b20 should detect 85.0°C as power-on reset, retry,
        and if persistent reject it by returning None.
        """
        sample_85 = "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=85000\n"
        with patch("builtins.open", mock_open(read_data=sample_85)):
            with patch("time.sleep"):  # Avoid waiting during unit test
                result = piUtils._parse_ds18b20("/fake/device/w1_slave", "primary")
                self.assertIsNone(result)

    def test_parse_ds18b20_recovers_after_initial_85c(self):
        """
        _parse_ds18b20 waits for conversion: if first attempt is 85.0°C
        but second attempt is a valid temperature (e.g. 26.4°C), returns valid temp.
        """
        data_85 = "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=85000\n"
        data_valid = "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=26437\n"

        # Mock open returning 85.0 on 1st attempt, then 26.43 on 2nd attempt
        m_open = mock_open()
        m_open.side_effect = [
            mock_open(read_data=data_85).return_value,
            mock_open(read_data=data_valid).return_value,
        ]

        with patch("builtins.open", m_open):
            with patch("time.sleep"):
                result = piUtils._parse_ds18b20("/fake/device/w1_slave", "primary")
                self.assertEqual(result, 26.44)

    def test_simulator_ignores_85c_sensor_reading(self):
        """
        simulator.generate_sensor_reading must reject 85.0°C and not overwrite
        current_temp or trigger cooling.
        """
        simulator.current_temp = 25.5
        with patch("app.sensors.simulator.read_temp_sensor", return_value=85.0):
            reading = generate_sensor_reading()
            # current_temp must not be 85.0
            self.assertNotEqual(reading["temperature"], 85.0)
            self.assertLess(reading["temperature"], 35.0)
            self.assertFalse(piUtils.coolingSystemActive)

    # -------------------------------------------------------------------------
    # 3. THREE CONSECUTIVE TEMPERATURE READINGS TESTS (>= and <= 0.5 DEVIATION)
    # -------------------------------------------------------------------------
    def test_cooling_triggers_only_after_three_consecutive_high_readings(self):
        """
        Cooling system must NOT trigger on 1 or 2 high readings;
        it must wait until three consecutive readings reach or exceed the threshold (>= setpoint + 0.5).
        """
        # Setpoint = 26.0, tolerance = 0.5 -> threshold = 26.5
        # Reading 1: 27.0 (>= 26.5) -> should NOT trigger cooling
        piUtils.applyTemperatureControl(temperature=27.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 1)

        # Reading 2: 27.2 (>= 26.5) -> should NOT trigger cooling yet
        piUtils.applyTemperatureControl(temperature=27.2, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 2)

        # Reading 3: 27.1 (>= 26.5) -> 3rd consecutive reading triggers cooling!
        piUtils.applyTemperatureControl(temperature=27.1, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertEqual(piUtils.last_temp_system, "cooling")

    def test_cooling_triggers_at_exact_half_degree_boundary_after_three_consecutive_readings(self):
        """
        Cooling must trigger when reading reaches EXACTLY setpoint + 0.5 (e.g. 32.5 with setpoint 32.0),
        not waiting until > 32.5, requiring 3 consecutive readings at or above 32.5.
        """
        # Setpoint = 32.0, tolerance = 0.5 -> threshold = 32.5
        # Reading 1: exactly 32.5 -> not triggered yet (1/3)
        piUtils.applyTemperatureControl(temperature=32.5, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 1)

        # Reading 2: exactly 32.5 -> not triggered yet (2/3)
        piUtils.applyTemperatureControl(temperature=32.5, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 2)

        # Fluctuation back into stable band (32.4C < 32.5C) -> counter resets to 0
        piUtils.applyTemperatureControl(temperature=32.4, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 0)

        # Reading 1 again: 32.5 -> not triggered (1/3)
        piUtils.applyTemperatureControl(temperature=32.5, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 1)

        # Reading 2 again: 32.5 -> not triggered (2/3)
        piUtils.applyTemperatureControl(temperature=32.5, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 2)

        # Reading 3: 32.5 -> 3rd consecutive reading triggers cooling!
        piUtils.applyTemperatureControl(temperature=32.5, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertEqual(piUtils.last_temp_system, "cooling")

    def test_fluctuation_resets_consecutive_high_readings_counter(self):
        """
        If temperature fluctuates back into the normal range before reaching 3 consecutive
        high readings, the counter must reset to 0 and cooling must NOT trigger.
        """
        # Reading 1: High
        piUtils.applyTemperatureControl(temperature=27.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 1)

        # Reading 2: Fluctuates back into stable range (26.2C)
        piUtils.applyTemperatureControl(temperature=26.2, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 0)

        # Reading 3: High again -> starts at count 1, does NOT trigger
        piUtils.applyTemperatureControl(temperature=27.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 1)

    def test_heating_triggers_only_after_three_consecutive_low_readings(self):
        """
        Heating system must NOT trigger on 1 or 2 low readings;
        it must wait until three consecutive readings drop below the threshold (<= setpoint - 0.5).
        """
        # Setpoint = 26.0, tolerance = 0.5 -> low threshold = 25.5
        # Reading 1: 25.0 (<= 25.5) -> should NOT trigger heating
        piUtils.applyTemperatureControl(temperature=25.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_low, 1)

        # Reading 2: 24.8 (<= 25.5) -> should NOT trigger heating yet
        piUtils.applyTemperatureControl(temperature=24.8, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_low, 2)

        # Reading 3: 24.9 (<= 25.5) -> 3rd consecutive reading triggers heating!
        piUtils.applyTemperatureControl(temperature=24.9, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils.last_temp_system, "heating")

    def test_heating_triggers_at_exact_half_degree_boundary_after_three_consecutive_readings(self):
        """
        Heating must trigger when reading reaches EXACTLY setpoint - 0.5 (e.g. 31.5 with setpoint 32.0),
        not waiting until < 31.5, requiring 3 consecutive readings at or below 31.5.
        """
        # Setpoint = 32.0, tolerance = 0.5 -> low threshold = 31.5
        # Reading 1: exactly 31.5 -> not triggered yet (1/3)
        piUtils.applyTemperatureControl(temperature=31.5, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_low, 1)

        # Reading 2: exactly 31.5 -> not triggered yet (2/3)
        piUtils.applyTemperatureControl(temperature=31.5, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_low, 2)

        # Fluctuation back into stable band (31.6C > 31.5C) -> counter resets to 0
        piUtils.applyTemperatureControl(temperature=31.6, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_low, 0)

        # Reading 1 again: 31.5 -> not triggered (1/3)
        piUtils.applyTemperatureControl(temperature=31.5, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_low, 1)

        # Reading 2 again: 31.5 -> not triggered (2/3)
        piUtils.applyTemperatureControl(temperature=31.5, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_low, 2)

        # Reading 3: 31.5 -> 3rd consecutive reading triggers heating!
        piUtils.applyTemperatureControl(temperature=31.5, setpoint=32.0, tolerance=0.5, secondary_temp=32.0)
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils.last_temp_system, "heating")

    def test_stable_reading_shuts_off_active_system_and_resets_counters(self):
        """
        Once temperature stabilizes within tolerance band, active systems turn OFF
        and counters reset.
        """
        # Trigger cooling with 3 readings
        for _ in range(3):
            piUtils.applyTemperatureControl(temperature=27.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertTrue(piUtils.coolingSystemActive)

        # 1 reading in stable band shuts off cooling
        piUtils.applyTemperatureControl(temperature=26.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertEqual(piUtils._temp_consecutive_high, 0)
        self.assertEqual(piUtils._temp_consecutive_low, 0)
        self.assertFalse(piUtils._heating_disabled_by_mixing)
        self.assertFalse(piUtils._cooling_disabled_by_mixing)

    # -------------------------------------------------------------------------
    # 4. MIXING TANK TEMPERATURE CONTROL TESTS (1.0°C DEVIATION)
    # -------------------------------------------------------------------------
    def test_heating_mixing_tank_overtemp_disables_heating_while_valve_remains_open(self):
        """
        When heating is enabled, if mixing tank reaches >= setpoint + 1.0 C,
        it must wait for 3 consecutive readings before disabling heating.
        The heating valve must remain OPEN and mixer must remain ON while heating is disabled.
        """
        # Trigger heating mode (setpoint = 26.0, threshold = 25.5, mixing limit = 27.0)
        for _ in range(3):
            piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.0)
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertTrue(piUtils.mixerActive)

        # Over-temp reading 1: mixing tank = 27.0 C (>= 27.0 C) -> heating still active (1/3)
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=27.0)
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_heat_disable, 1)

        # Over-temp reading 2: mixing tank = 27.2 C -> heating still active (2/3)
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=27.2)
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_heat_disable, 2)

        # Fluctuation below 27.0 C (26.5 C) -> resets over-temp counter
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.5)
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_heat_disable, 0)

        # 3 continuous over-temp readings: 27.1, 27.3, 27.0
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=27.1)
        self.assertTrue(piUtils.heatingSystemActive)
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=27.3)
        self.assertTrue(piUtils.heatingSystemActive)
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=27.0)

        # On the 3rd continuous reading, heating system is DISABLED, but heating valve remains OPEN and mixer remains ON
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertTrue(piUtils.mixerActive)
        self.assertTrue(piUtils._heating_disabled_by_mixing)

    def test_heating_mixing_tank_recovery_reenables_heating(self):
        """
        When heating is disabled due to mixing tank over-temp, it must wait for
        3 consecutive readings below setpoint + 1.0 C before re-enabling heating.
        """
        # Trigger heating mode and then disable it with 3 over-temp readings (mixing limit = 27.0 C)
        for _ in range(3):
            piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.0)
        for _ in range(3):
            piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=27.2)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertTrue(piUtils._heating_disabled_by_mixing)

        # Recovery reading 1: mixing tank = 26.8 C (< 27.0 C) -> heating still disabled (1/3)
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.8)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_heat_enable, 1)

        # Recovery reading 2: mixing tank = 26.7 C -> heating still disabled (2/3)
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.7)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_heat_enable, 2)

        # Fluctuation back above limit (27.1 C) -> resets recovery counter
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=27.1)
        self.assertFalse(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_heat_enable, 0)

        # 3 continuous recovery readings: 26.5, 26.6, 26.4
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.5)
        self.assertFalse(piUtils.heatingSystemActive)
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.6)
        self.assertFalse(piUtils.heatingSystemActive)
        piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.4)

        # On the 3rd continuous reading, heating system is RE-ENABLED
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.heatingValveOpen)
        self.assertFalse(piUtils._heating_disabled_by_mixing)

    def test_cooling_mixing_tank_undertemp_disables_cooling_while_valve_remains_open(self):
        """
        When cooling is enabled, if mixing tank reaches <= setpoint - 1.0 C,
        it must wait for 3 consecutive readings before disabling cooling.
        The cooling valve must remain OPEN and mixer remains ON while cooling is disabled.
        """
        # Trigger cooling mode (setpoint = 26.0, threshold = 26.5, mixing limit = 25.0)
        for _ in range(3):
            piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=27.0)
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertTrue(piUtils.mixerActive)

        # Under-temp reading 1: mixing tank = 25.0 C (<= 25.0 C) -> cooling still active (1/3)
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.0)
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_cool_disable, 1)

        # Under-temp reading 2: mixing tank = 24.8 C -> cooling still active (2/3)
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=24.8)
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_cool_disable, 2)

        # Fluctuation above 25.0 C (25.5 C) -> resets under-temp counter
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.5)
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_cool_disable, 0)

        # 3 continuous under-temp readings: 24.9, 24.7, 25.0
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=24.9)
        self.assertTrue(piUtils.coolingSystemActive)
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=24.7)
        self.assertTrue(piUtils.coolingSystemActive)
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.0)

        # On the 3rd continuous reading, cooling system is DISABLED, but cooling valve remains OPEN and mixer remains ON
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertTrue(piUtils.mixerActive)
        self.assertTrue(piUtils._cooling_disabled_by_mixing)

    def test_cooling_mixing_tank_recovery_reenables_cooling(self):
        """
        When cooling is disabled due to mixing tank under-temp, it must wait for
        3 consecutive readings above setpoint - 1.0 C before re-enabling cooling.
        """
        # Trigger cooling mode and disable it with 3 under-temp readings (mixing limit = 25.0 C)
        for _ in range(3):
            piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=27.0)
        for _ in range(3):
            piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=24.5)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertTrue(piUtils._cooling_disabled_by_mixing)

        # Recovery reading 1: mixing tank = 25.2 C (> 25.0 C) -> cooling still disabled (1/3)
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.2)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_cool_enable, 1)

        # Recovery reading 2: mixing tank = 25.4 C -> cooling still disabled (2/3)
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.4)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_cool_enable, 2)

        # Fluctuation back below limit (24.8 C) -> resets recovery counter
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=24.8)
        self.assertFalse(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertEqual(piUtils._mix_consecutive_cool_enable, 0)

        # 3 continuous recovery readings: 25.3, 25.5, 25.6
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.3)
        self.assertFalse(piUtils.coolingSystemActive)
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.5)
        self.assertFalse(piUtils.coolingSystemActive)
        piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.6)

        # On the 3rd continuous reading, cooling system is RE-ENABLED
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.coolingValveOpen)
        self.assertFalse(piUtils._cooling_disabled_by_mixing)


if __name__ == "__main__":
    unittest.main()
