import unittest
from unittest.mock import MagicMock, patch
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
import app.api.piUtils as piUtils


class TestMixerAndHeating(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Reset states
        piUtils.coolingSystem(False)
        piUtils.heatingSystem(False)
        piUtils.mixer(False)
        if piUtils._mixer_timer is not None:
            piUtils._mixer_timer.cancel()
            piUtils._mixer_timer = None
        piUtils.cancel_pump_timers()

        piUtils.coolingSystemActive = False
        piUtils.heatingSystemActive = False
        piUtils.mixerActive = False
        piUtils.heaterActive = False
        piUtils.heatingPumpActive = False
        piUtils.coolingPumpActive = False
        piUtils.reset_temperature_control_state()

    def tearDown(self):
        piUtils.coolingSystem(False)
        piUtils.heatingSystem(False)
        piUtils.mixer(False)
        if piUtils._mixer_timer is not None:
            piUtils._mixer_timer.cancel()
            piUtils._mixer_timer = None
        piUtils.cancel_pump_timers()
        piUtils.heatingPump(False)
        piUtils.coolingPump(False)

        self.app_context.pop()

    def wait_for(self, condition, timeout=1.0, interval=0.01):
        start = time.time()
        while time.time() - start < timeout:
            if condition():
                return True
            time.sleep(interval)
        return condition()

    def test_mixer_enables_when_cooling_turns_on(self):
        """Test mixer enables immediately when cooling system turns ON."""
        self.assertFalse(piUtils.mixerActive)
        piUtils.coolingSystem(True)
        self.assertTrue(piUtils.coolingSystemActive)
        self.assertTrue(piUtils.mixerActive)

    def test_mixer_enables_when_heating_turns_on(self):
        """Test mixer enables immediately when heating system turns ON."""
        self.assertFalse(piUtils.mixerActive)
        piUtils.heatingSystem(True)
        self.assertTrue(piUtils.heatingSystemActive)
        self.assertTrue(piUtils.mixerActive)

    def test_mixer_delayed_shutoff_after_cooling_disabled(self):
        """Test mixer remains ON after cooling is disabled and shuts off after the delay."""
        orig_delay = piUtils.MIXER_SHUTDOWN_DELAY
        piUtils.MIXER_SHUTDOWN_DELAY = 0.1  # 100ms for test
        try:
            piUtils.coolingSystem(True)
            self.assertTrue(piUtils.mixerActive)

            # Disable cooling
            piUtils.coolingSystem(False)
            self.assertFalse(piUtils.coolingSystemActive)
            # Mixer should still be ON immediately after disable
            self.assertTrue(piUtils.mixerActive)
            self.assertIsNotNone(piUtils._mixer_timer)

            # Wait for shutdown delay to expire
            self.assertTrue(self.wait_for(lambda: not piUtils.mixerActive, timeout=0.3))
            self.assertIsNone(piUtils._mixer_timer)
        finally:
            piUtils.MIXER_SHUTDOWN_DELAY = orig_delay

    def test_mixer_delay_cancelled_if_system_re_enabled(self):
        """Test mixer shutdown timer is cancelled if cooling or heating turns back on."""
        orig_delay = piUtils.MIXER_SHUTDOWN_DELAY
        piUtils.MIXER_SHUTDOWN_DELAY = 0.2
        try:
            piUtils.coolingSystem(True)
            piUtils.coolingSystem(False)
            self.assertTrue(piUtils.mixerActive)
            self.assertIsNotNone(piUtils._mixer_timer)

            # Turn heating on before timer expires
            time.sleep(0.05)
            piUtils.heatingSystem(True)
            # Timer should be cancelled and mixer should remain ON
            self.assertIsNone(piUtils._mixer_timer)
            self.assertTrue(piUtils.mixerActive)

            # Wait longer than original delay
            time.sleep(0.2)
            self.assertTrue(piUtils.mixerActive)
        finally:
            piUtils.MIXER_SHUTDOWN_DELAY = orig_delay

    def test_mixer_remains_on_if_other_system_active(self):
        """Test mixer stays ON without shutoff timer if one system is disabled while the other is still active."""
        orig_delay = piUtils.MIXER_SHUTDOWN_DELAY
        piUtils.MIXER_SHUTDOWN_DELAY = 0.1
        try:
            piUtils.coolingSystem(True)
            piUtils.heatingSystem(True)
            self.assertTrue(piUtils.mixerActive)

            # Disable cooling while heating remains active
            piUtils.coolingSystem(False)
            self.assertIsNone(piUtils._mixer_timer)
            self.assertTrue(piUtils.mixerActive)

            # Wait delay
            time.sleep(0.15)
            self.assertTrue(piUtils.mixerActive)
        finally:
            piUtils.MIXER_SHUTDOWN_DELAY = orig_delay

    def test_heater_sensor_based_activation_and_deactivation(self):
        """
        Test sensor-based heater control (replaces timed pulse intervals):
        1. When heating tank sensor temp < setpoint, heater relay activates to add heat.
        2. When heating tank sensor temp >= setpoint, heater relay deactivates to cool down.
        """
        piUtils.applyHeatingTankControl(heating_tank_temp=35.0, setpoint=40.0)
        self.assertTrue(piUtils.heaterActive)

        piUtils.applyHeatingTankControl(heating_tank_temp=39.9, setpoint=40.0)
        self.assertTrue(piUtils.heaterActive)

        piUtils.applyHeatingTankControl(heating_tank_temp=40.0, setpoint=40.0)
        self.assertFalse(piUtils.heaterActive)

        piUtils.applyHeatingTankControl(heating_tank_temp=42.5, setpoint=40.0)
        self.assertFalse(piUtils.heaterActive)

    def test_heater_stays_off_when_heating_tank_sensor_unavailable(self):
        """Test that if heating tank sensor returns None, heater relay is safely turned OFF."""
        piUtils.applyHeatingTankControl(heating_tank_temp=35.0, setpoint=40.0)
        self.assertTrue(piUtils.heaterActive)

        piUtils.applyHeatingTankControl(heating_tank_temp=None, setpoint=40.0)
        self.assertFalse(piUtils.heaterActive)

    def test_rpi_gpio_mixer_and_heater_outputs(self):
        """Test GPIO pin outputs on RPi mode for mixerPin and heatingSystemPin."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, 'isRpiPresent', True), \
             patch.object(piUtils, 'GPIO', mock_gpio):

            piUtils.setMixer(True)
            mock_gpio.output.assert_called_with(piUtils.mixerPin, 0)

            piUtils.setMixer(False)
            mock_gpio.output.assert_called_with(piUtils.mixerPin, 1)

            piUtils._setRpiHeaterRelay(True)
            mock_gpio.output.assert_called_with(piUtils.heatingSystemPin, 1)

            piUtils._setRpiHeaterRelay(False)
            mock_gpio.output.assert_called_with(piUtils.heatingSystemPin, 0)

    def test_cleanup_gpio(self):
        """Test cleanupGpio cancels mixer timer, stops heating, turns off mixer/heater, and cleans up GPIO."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        piUtils.mixerActive = True
        piUtils.heatingSystemActive = True
        piUtils.heaterActive = True

        with patch.object(piUtils, 'isRpiPresent', True), \
             patch.object(piUtils, 'GPIO', mock_gpio):

            piUtils.cleanupGpio()
            self.assertFalse(piUtils.mixerActive)
            self.assertFalse(piUtils.heatingSystemActive)
            self.assertFalse(piUtils.heaterActive)
            mock_gpio.output.assert_any_call(piUtils.mixerPin, 1)
            mock_gpio.output.assert_any_call(piUtils.heatingSystemPin, 0)
            mock_gpio.cleanup.assert_called_once()

    def test_apply_temperature_control_activates_heating_and_mixer(self):
        """Test applyTemperatureControl activates heating and mixer when temperature is below setpoint."""
        orig_delay = piUtils.MIXER_SHUTDOWN_DELAY
        piUtils.MIXER_SHUTDOWN_DELAY = 0.1
        piUtils.reset_temperature_control_state()
        try:
            # Temperature = 24.0, setpoint = 26.0, tolerance = 0.5 (low = 25.5, requires 3 consecutive readings)
            for _ in range(3):
                piUtils.applyTemperatureControl(24.0, 26.0, tolerance=0.5, secondary_temp=24.2)
            self.assertTrue(piUtils.heatingSystemActive)
            self.assertTrue(piUtils.heatingValveOpen)
            self.assertFalse(piUtils.coolingSystemActive)
            self.assertFalse(piUtils.coolingValveOpen)
            self.assertTrue(piUtils.mixerActive)

            # Temperature returns to stable = 26.0
            piUtils.applyTemperatureControl(26.0, 26.0, tolerance=0.5, secondary_temp=26.0)
            self.assertFalse(piUtils.heatingSystemActive)
            self.assertFalse(piUtils.heatingValveOpen)
            # Mixer should still be active immediately due to 20s run-on delay
            self.assertTrue(piUtils.mixerActive)

            # After delay expires, mixer turns off
            self.assertTrue(self.wait_for(lambda: not piUtils.mixerActive, timeout=0.3))
        finally:
            piUtils.MIXER_SHUTDOWN_DELAY = orig_delay

    def test_mixing_fan_remains_on_when_heating_disabled_by_mixing_tank_overtemp(self):
        """
        Even when the heating system is disabled because the mixing tank exceeded the 2 C limit,
        the mixing fan (mixer motor) must remain ON, and must not shut down after the delay.
        """
        orig_delay = piUtils.MIXER_SHUTDOWN_DELAY
        piUtils.MIXER_SHUTDOWN_DELAY = 0.05
        piUtils.reset_temperature_control_state()
        try:
            # 1. Trigger heating mode with 3 low readings on main tank
            for _ in range(3):
                piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=25.0)
            self.assertTrue(piUtils.heatingSystemActive)
            self.assertTrue(piUtils.heatingValveOpen)
            self.assertTrue(piUtils.mixerActive)
            self.assertTrue(piUtils.mixingFanActive)

            # 2. Exceed 2 C limit in mixing tank (3 consecutive readings at 28.2 C >= 28.0 C)
            for _ in range(3):
                piUtils.applyTemperatureControl(temperature=24.0, setpoint=26.0, tolerance=0.5, secondary_temp=28.2)

            # Heating system disabled, heating valve still open
            self.assertFalse(piUtils.heatingSystemActive)
            self.assertTrue(piUtils.heatingValveOpen)
            self.assertTrue(piUtils._heating_disabled_by_mixing)

            # Mixing fan must REMAIN ON
            self.assertTrue(piUtils.mixerActive)
            self.assertTrue(piUtils.mixingFanActive)
            self.assertIsNone(piUtils._mixer_timer)

            # Wait longer than shutdown delay to guarantee mixer does NOT shut off
            time.sleep(0.1)
            self.assertTrue(piUtils.mixerActive)
            self.assertTrue(piUtils.mixingFanActive)

            # 3. Main tank returns to stable temperature (26.0 C)
            piUtils.applyTemperatureControl(temperature=26.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
            self.assertFalse(piUtils.heatingSystemActive)
            self.assertFalse(piUtils._heating_disabled_by_mixing)

            # Now that temperature control is complete, mixer shuts off after delay
            self.assertTrue(self.wait_for(lambda: not piUtils.mixerActive, timeout=0.3))
            self.assertFalse(piUtils.mixingFanActive)
        finally:
            piUtils.MIXER_SHUTDOWN_DELAY = orig_delay

    def test_mixing_fan_remains_on_when_cooling_disabled_by_mixing_tank_undertemp(self):
        """
        Even when the cooling system is disabled because the mixing tank dropped 2 C below setpoint,
        the mixing fan (mixer motor) must remain ON, and must not shut down after the delay.
        """
        orig_delay = piUtils.MIXER_SHUTDOWN_DELAY
        piUtils.MIXER_SHUTDOWN_DELAY = 0.05
        piUtils.reset_temperature_control_state()
        try:
            # 1. Trigger cooling mode with 3 high readings on main tank
            for _ in range(3):
                piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=27.0)
            self.assertTrue(piUtils.coolingSystemActive)
            self.assertTrue(piUtils.coolingValveOpen)
            self.assertTrue(piUtils.mixerActive)
            self.assertTrue(piUtils.mixingFanActive)

            # 2. Exceed 2 C limit in mixing tank (3 consecutive readings at 23.5 C <= 24.0 C)
            for _ in range(3):
                piUtils.applyTemperatureControl(temperature=28.0, setpoint=26.0, tolerance=0.5, secondary_temp=23.5)

            # Cooling system disabled, cooling valve still open
            self.assertFalse(piUtils.coolingSystemActive)
            self.assertTrue(piUtils.coolingValveOpen)
            self.assertTrue(piUtils._cooling_disabled_by_mixing)

            # Mixing fan must REMAIN ON
            self.assertTrue(piUtils.mixerActive)
            self.assertTrue(piUtils.mixingFanActive)
            self.assertIsNone(piUtils._mixer_timer)

            # Wait longer than shutdown delay to guarantee mixer does NOT shut off
            time.sleep(0.1)
            self.assertTrue(piUtils.mixerActive)
            self.assertTrue(piUtils.mixingFanActive)

            # 3. Main tank returns to stable temperature (26.0 C)
            piUtils.applyTemperatureControl(temperature=26.0, setpoint=26.0, tolerance=0.5, secondary_temp=26.0)
            self.assertFalse(piUtils.coolingSystemActive)
            self.assertFalse(piUtils._cooling_disabled_by_mixing)

            # Now that temperature control is complete, mixer shuts off after delay
            self.assertTrue(self.wait_for(lambda: not piUtils.mixerActive, timeout=0.3))
            self.assertFalse(piUtils.mixingFanActive)
        finally:
            piUtils.MIXER_SHUTDOWN_DELAY = orig_delay


if __name__ == "__main__":
    unittest.main()
