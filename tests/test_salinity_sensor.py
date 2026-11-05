import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.salinity_sensor import SalinitySensorService


class TestSalinitySensorService(unittest.TestCase):
    def test_voltage_to_salinity_conversion_at_25c(self):
        mock_adc = MagicMock()
        # Mock 0.00255V (V_0) -> Salinity 0.0 ppt
        mock_adc.read_filtered_voltage.return_value = 0.00255
        service = SalinitySensorService(adc_service=mock_adc)
        self.assertAlmostEqual(service.read_salinity(temp_c=25.0), 0.0)

        # Mock 0.88V at 25C -> 38.265306 * (0.88 - 0.00255) = 33.58 ppt
        mock_adc.read_filtered_voltage.return_value = 0.88
        service = SalinitySensorService(adc_service=mock_adc)
        self.assertAlmostEqual(service.read_salinity(temp_c=25.0), 33.58)

    def test_voltage_to_salinity_conversion_with_temp_compensation(self):
        mock_adc = MagicMock()
        # Mock 0.88V at 30C (alpha=0.02):
        # denom = 1.0 + 0.02 * 5.0 = 1.10
        # live_v25 = (0.87745 / 1.10) + 0.00255 = 0.8002318
        # salinity = 38.265306 * (0.8002318 - 0.00255) = 30.52 ppt
        mock_adc.read_filtered_voltage.return_value = 0.88
        service = SalinitySensorService(adc_service=mock_adc)
        self.assertAlmostEqual(service.read_salinity(temp_c=30.0), 30.52)

    @patch("app.api.piUtils.read_temp_sensor")
    def test_read_salinity_uses_main_tank_ds18b20_temp(self, mock_read_temp):
        mock_adc = MagicMock()
        mock_adc.read_filtered_voltage.return_value = 0.88
        mock_read_temp.return_value = 30.0
        service = SalinitySensorService(adc_service=mock_adc)
        # Without passing temp_c explicitly, it should fetch live temp from read_temp_sensor()
        salinity = service.read_salinity()
        mock_read_temp.assert_called_once()
        self.assertAlmostEqual(salinity, 30.52)

    def test_salinity_clamping(self):
        mock_adc = MagicMock()
        # Voltage that would result in salinity > 50
        mock_adc.read_filtered_voltage.return_value = 6.0
        service = SalinitySensorService(adc_service=mock_adc)
        self.assertEqual(service.read_salinity(temp_c=25.0), 50.0)

        # Voltage that would result in salinity < 0
        mock_adc.read_filtered_voltage.return_value = -1.0
        service = SalinitySensorService(adc_service=mock_adc)
        self.assertEqual(service.read_salinity(temp_c=25.0), 0.0)

    @patch("app.services.salinity_sensor.logger")
    def test_salinity_sensor_failure_handling(self, mock_logger):
        mock_adc = MagicMock()
        mock_adc.read_filtered_voltage.side_effect = RuntimeError("Hardware failure")
        service = SalinitySensorService(adc_service=mock_adc)

        # Should return None instead of crashing
        salinity = service.read_salinity()
        self.assertIsNone(salinity)
        # Should log exception
        mock_logger.exception.assert_called_with("Failed to read Salinity sensor")


if __name__ == "__main__":
    unittest.main()
