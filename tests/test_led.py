import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
import app.api.piUtils as piUtils
from app.models import update_control_settings, get_control_settings

# Use PWM PIN 18 as configured for the LED MOSFET driver
PWM_PIN = 18
MAX_LUX = 3000
LED_PWM_FREQ = 1000


class TestLEDOperation(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Reset LED state before each test
        piUtils.currentLedLux = 0.0
        piUtils.currentLedDutyCycle = 0.0
        piUtils.ledPwm = None
        piUtils.mosfet = None

    def tearDown(self):
        piUtils.set_lux(0)
        self.app_context.pop()

    def test_pin_and_constants(self):
        """Verify that LED PWM pin is 18, frequency is 1000Hz, and max lux is 3000."""
        self.assertEqual(piUtils.ledPWMPin, 18)
        self.assertEqual(piUtils.PWM_PIN, 18)
        self.assertEqual(piUtils.LED_PWM_FREQ, 1000)
        self.assertEqual(piUtils.MAX_LUX, 3000)

    def test_dummy_set_lux_levels(self):
        """Test dummy mode set_lux for hatchery levels (0, 100, 500, 1000, 3000)."""
        hatchery_levels = [
            (0, 0.0),
            (100, (100 / 3000) * 100),
            (500, (500 / 3000) * 100),
            (1000, (1000 / 3000) * 100),
            (3000, 100.0),
        ]

        for lux, expected_duty in hatchery_levels:
            piUtils.setDummyLux(lux)
            self.assertEqual(piUtils.currentLedLux, float(lux))
            self.assertAlmostEqual(piUtils.currentLedDutyCycle, expected_duty, places=2)

    def test_dummy_duty_cycle_clamping(self):
        """Test duty cycle clamping for out-of-range lux values."""
        # Negative lux should clamp to 0%
        piUtils.setDummyLux(-50)
        self.assertEqual(piUtils.currentLedDutyCycle, 0.0)

        # Excess lux (>3000) should clamp to 100%
        piUtils.setDummyLux(4500)
        self.assertEqual(piUtils.currentLedDutyCycle, 100.0)

    def test_rpi_gpio_pwm_control(self):
        """Test RPi mode GPIO PWM initialization and ChangeDutyCycle output on PIN 18."""
        mock_gpio = MagicMock()
        mock_pwm_instance = MagicMock()
        mock_gpio.PWM.return_value = mock_pwm_instance

        with patch.object(piUtils, "isRpiPresent", True), patch.object(piUtils, "GPIO", mock_gpio):
            # Test setting 1500 lux (50% power)
            piUtils.set_lux(1500)

            # Verify PWM was created on pin 18 at 1000Hz
            mock_gpio.PWM.assert_called_with(18, 1000)
            mock_pwm_instance.start.assert_called_with(0)
            mock_pwm_instance.ChangeDutyCycle.assert_called_with(50.0)
            self.assertEqual(piUtils.currentLedLux, 1500.0)
            self.assertAlmostEqual(piUtils.currentLedDutyCycle, 50.0)

            # Test setting 3000 lux (100% power)
            piUtils.set_lux(3000)
            mock_pwm_instance.ChangeDutyCycle.assert_called_with(100.0)

            # Test setting 0 lux (0% power)
            piUtils.set_lux(0)
            mock_pwm_instance.ChangeDutyCycle.assert_called_with(0.0)

    def test_api_controls_sliders_sets_led_intensity(self):
        """Test POST /api/controls/sliders updates DB and triggers set_lux."""
        with patch.object(piUtils, "set_lux") as mock_set_lux:
            response = self.client.post("/api/controls/sliders", json={"led_intensity": 1000.0})
            self.assertEqual(response.status_code, 200)
            mock_set_lux.assert_called_once_with(1000.0)

            data = response.get_json()
            self.assertEqual(data["sliders"]["led_intensity"], 1000.0)

    def test_api_controls_led_endpoint(self):
        """Test POST /api/controls/led endpoint for direct lux control."""
        with patch.object(piUtils, "set_lux") as mock_set_lux:
            # Test with 'lux'
            response = self.client.post("/api/controls/led", json={"lux": 500.0})
            self.assertEqual(response.status_code, 200)
            mock_set_lux.assert_called_with(500.0)

            # Test with 'led_intensity'
            response = self.client.post("/api/controls/led", json={"led_intensity": 3000.0})
            self.assertEqual(response.status_code, 200)
            mock_set_lux.assert_called_with(3000.0)

            # Test error on missing parameter
            response = self.client.post("/api/controls/led", json={})
            self.assertEqual(response.status_code, 400)

    def test_initialize_led_from_db(self):
        """Test initializeLed restores stored setting from thresholds table."""
        update_control_settings({"led_intensity": 500.0})

        with patch.object(piUtils, "set_lux") as mock_set_lux:
            piUtils.initializeLed(self.app)
            mock_set_lux.assert_called_with(500.0)

    def test_cleanup_gpio_stops_pwm(self):
        """Test cleanupGpio safely stops the LED PWM signal and cleans up GPIO."""
        mock_gpio = MagicMock()
        mock_pwm = MagicMock()

        with patch.object(piUtils, "isRpiPresent", True), patch.object(piUtils, "GPIO", mock_gpio):
            piUtils.ledPwm = mock_pwm
            piUtils.cleanupGpio()

            mock_pwm.ChangeDutyCycle.assert_called_with(0)
            mock_pwm.stop.assert_called_once()
            mock_gpio.cleanup.assert_called_once()


def run_hardware_test(pin=PWM_PIN):
    """
    Standalone hardware test sequence on Raspberry Pi.
    Cycles through hatchery lighting levels: [0, 100, 500, 1000, 3000] lux.
    """
    import time
    try:
        import RPi.GPIO as GPIO
    except ImportError:
        print("RPi.GPIO not installed. Hardware test can only run on Raspberry Pi.")
        return

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(pin, GPIO.OUT)

    mosfet = GPIO.PWM(pin, LED_PWM_FREQ)
    mosfet.start(0)

    def _set_lux(target_lux):
        duty_cycle = (target_lux / MAX_LUX) * 100.0
        duty_cycle = max(0.0, min(100.0, duty_cycle))
        mosfet.ChangeDutyCycle(duty_cycle)
        print(f"Lights set to {target_lux} lux ({duty_cycle:.1f}% power)")

    try:
        print(f"Starting hatchery lighting test on PWM PIN {pin}...")
        hatchery_levels = [0, 100, 500, 1000, 3000]
        for lux in hatchery_levels:
            _set_lux(lux)
            time.sleep(5)
        print("Test complete. Holding at max brightness for 5 seconds...")
        time.sleep(5)
    except KeyboardInterrupt:
        print("\nScript interrupted. Turning off lights.")
    finally:
        mosfet.stop()
        GPIO.cleanup(pin)
        print("Hardware test finished and GPIO cleaned up.")


if __name__ == "__main__":
    if "--hardware" in sys.argv or "--run-hardware" in sys.argv:
        run_hardware_test(PWM_PIN)
    else:
        unittest.main()
