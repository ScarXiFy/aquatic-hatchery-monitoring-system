import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.models import save_motor_state, load_motor_state, init_motor_state
import app.api.piUtils as piUtils


class TestMotorInitAndPersistence(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app_context = self.app.app_context()
        self.app_context.push()
        save_motor_state("bleed_valve", 0)

    def tearDown(self):
        save_motor_state("bleed_valve", 0)
        self.app_context.pop()

    def test_database_motor_state_crud(self):
        """Test save_motor_state, load_motor_state, and init_motor_state."""
        init_motor_state()
        pos = load_motor_state("bleed_valve")
        self.assertIsNotNone(pos)
        self.assertEqual(pos, 0)

        # Update position to 2
        save_motor_state("bleed_valve", 2)
        pos2 = load_motor_state("bleed_valve")
        self.assertEqual(pos2, 2)

    def test_motor_init_when_saved_position_greater_than_zero(self):
        """Test initializeBleedValveMotor enables driver and resets motor CW when saved position > 0."""
        save_motor_state("bleed_valve", 2)
        piUtils.motorInit = False

        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, 'isRpiPresent', True), \
             patch.object(piUtils, 'GPIO', mock_gpio), \
             patch.object(piUtils, 'CW', 1), \
             patch.object(piUtils, 'CCW', 0), \
             patch('time.sleep', return_value=None):

            piUtils.initializeBleedValveMotor()
            self.assertTrue(piUtils.motorInit)
            self.assertEqual(piUtils.doBleedValvePercent, 0.0)
            self.assertEqual(load_motor_state("bleed_valve"), 0)

            # Verify ENABLE pin is pulled LOW to enable driver
            mock_gpio.output.assert_any_call(piUtils.bleedValveEnablePin, 0)
            # Verify CW output and 36 step pulses (2 * 18 = 36)
            mock_gpio.output.assert_any_call(piUtils.bleedValveDirPin, 1)
            step_high_calls = [c for c in mock_gpio.output.call_args_list if c == unittest.mock.call(piUtils.bleedValveStepPin, 1)]
            self.assertEqual(len(step_high_calls), 36)

    def test_set_do_bleed_valve_blocked_when_not_initialized(self):
        """Test setDoBleedValveMotor skips movement if motorInit is False."""
        piUtils.motorInit = False
        piUtils.doBleedValvePercent = 0.0

        with patch('logging.critical') as mock_critical:
            piUtils.setDoBleedValveMotor(33.0)
            self.assertEqual(piUtils.doBleedValvePercent, 0.0)
            mock_critical.assert_called_with("[RPI] Bleed valve motor not initialized! Movement skipped.")

    def test_set_do_bleed_valve_persists_state_after_movement(self):
        """Test setDoBleedValveMotor drives enable pin LOW, updates percent, and persists state to DB."""
        piUtils.motorInit = True
        piUtils.doBleedValvePercent = 0.0

        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, 'isRpiPresent', True), \
             patch.object(piUtils, 'GPIO', mock_gpio), \
             patch.object(piUtils, 'CW', 1), \
             patch.object(piUtils, 'CCW', 0), \
             patch('time.sleep', return_value=None):

            piUtils.setDoBleedValveMotor(66.0)
            self.assertEqual(piUtils.doBleedValvePercent, 66.0)
            self.assertEqual(load_motor_state("bleed_valve"), 2)
            mock_gpio.output.assert_any_call(piUtils.bleedValveEnablePin, 0)

    def test_enable_and_disable_bleed_valve_motor(self):
        """Test enableBleedValveMotor and disableBleedValveMotor pin outputs and flags."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, 'isRpiPresent', True), \
             patch.object(piUtils, 'GPIO', mock_gpio):

            piUtils.enableBleedValveMotor()
            self.assertTrue(piUtils.bleedValveDriverEnabled)
            mock_gpio.output.assert_called_with(piUtils.bleedValveEnablePin, 0)

            piUtils.disableBleedValveMotor()
            self.assertFalse(piUtils.bleedValveDriverEnabled)
            mock_gpio.output.assert_called_with(piUtils.bleedValveEnablePin, 1)

    def test_cleanup_gpio(self):
        """Test cleanupGpio sets ENABLE pin HIGH and calls GPIO.cleanup()."""
        mock_gpio = MagicMock()
        mock_gpio.HIGH = 1
        mock_gpio.LOW = 0

        with patch.object(piUtils, 'isRpiPresent', True), \
             patch.object(piUtils, 'GPIO', mock_gpio):

            piUtils.cleanupGpio()
            self.assertFalse(piUtils.bleedValveDriverEnabled)
            mock_gpio.output.assert_called_with(piUtils.bleedValveEnablePin, 1)
            mock_gpio.cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
