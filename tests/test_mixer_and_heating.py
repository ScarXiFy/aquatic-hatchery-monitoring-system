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
        if piUtils._heating_thread is not None and piUtils._heating_thread.is_alive():
            piUtils._heating_stop_event.set()
            piUtils._heating_thread.join(timeout=1.0)
            piUtils._heating_thread = None

        piUtils.coolingSystemActive = False
        piUtils.heatingSystemActive = False
        piUtils.mixerActive = False
        piUtils.heaterActive = False

    def tearDown(self):
        piUtils.coolingSystem(False)
        piUtils.heatingSystem(False)
        piUtils.mixer(False)
        if piUtils._mixer_timer is not None:
            piUtils._mixer_timer.cancel()
            piUtils._mixer_timer = None
        if piUtils._heating_thread is not None and piUtils._heating_thread.is_alive():
            piUtils._heating_stop_event.set()
            piUtils._heating_thread.join(timeout=1.0)
            piUtils._heating_thread = None

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

    def test_heater_pulse_and_periodic_recheck(self):
        """
        Test that when heating is needed:
        1. Heater relay activates for 1s (shortened for test), then immediately disables.
        2. After 30s (shortened for test), if heating is still needed, heater activates again.
        3. When heating is disabled, heater stops and no further pulses occur.
        """
        orig_pulse = piUtils.HEATER_PULSE_DURATION
        orig_interval = piUtils.HEATER_CHECK_INTERVAL

        piUtils.HEATER_PULSE_DURATION = 0.05   # 50 ms
        piUtils.HEATER_CHECK_INTERVAL = 0.10   # 100 ms

        try:
            piUtils.heatingSystem(True)
            self.assertTrue(piUtils.heatingSystemActive)
            # Heater relay should be active immediately during pulse
            self.assertTrue(self.wait_for(lambda: piUtils.heaterActive, timeout=0.1))

            # After pulse duration (50ms), heater relay should be disabled
            self.assertTrue(self.wait_for(lambda: not piUtils.heaterActive, timeout=0.2))

            # Wait until 100ms check interval elapses and next pulse triggers
            self.assertTrue(self.wait_for(lambda: piUtils.heaterActive, timeout=0.3))

            # Disable heating system
            piUtils.heatingSystem(False)
            self.assertFalse(piUtils.heatingSystemActive)
            self.assertFalse(piUtils.heaterActive)

            # Wait to ensure no further pulses occur
            time.sleep(0.15)
            self.assertFalse(piUtils.heaterActive)
        finally:
            piUtils.HEATER_PULSE_DURATION = orig_pulse
            piUtils.HEATER_CHECK_INTERVAL = orig_interval

    def test_heater_immediate_disable_when_heating_stops_during_pulse(self):
        """Test that calling heatingSystem(False) during a pulse immediately disables the heater."""
        orig_pulse = piUtils.HEATER_PULSE_DURATION
        orig_interval = piUtils.HEATER_CHECK_INTERVAL
        piUtils.HEATER_PULSE_DURATION = 0.5
        piUtils.HEATER_CHECK_INTERVAL = 1.0

        try:
            piUtils.heatingSystem(True)
            self.assertTrue(self.wait_for(lambda: piUtils.heaterActive, timeout=0.2))

            # Stop heating during the pulse
            piUtils.heatingSystem(False)
            self.assertFalse(piUtils.heaterActive)
            self.assertFalse(piUtils.heatingSystemActive)
        finally:
            piUtils.HEATER_PULSE_DURATION = orig_pulse
            piUtils.HEATER_CHECK_INTERVAL = orig_interval

    def test_rpi_gpio_mixer_and_heater_outputs(self):
        """Test GPIO pin outputs on RPi mode for mixerPin and heatingSystemPin."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, 'isRpiPresent', True), \
             patch.object(piUtils, 'GPIO', mock_gpio):

            piUtils.setMixer(True)
            mock_gpio.output.assert_called_with(piUtils.mixerPin, 1)

            piUtils.setMixer(False)
            mock_gpio.output.assert_called_with(piUtils.mixerPin, 0)

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
            mock_gpio.output.assert_any_call(piUtils.mixerPin, 0)
            mock_gpio.output.assert_any_call(piUtils.heatingSystemPin, 0)
            mock_gpio.cleanup.assert_called_once()

    def test_apply_temperature_control_activates_heating_and_mixer(self):
        """Test applyTemperatureControl activates heating and mixer when temperature is below setpoint."""
        orig_delay = piUtils.MIXER_SHUTDOWN_DELAY
        piUtils.MIXER_SHUTDOWN_DELAY = 0.1
        try:
            # Temperature = 24.0, setpoint = 26.0, tolerance = 0.5 (low = 25.5)
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


if __name__ == "__main__":
    unittest.main()
