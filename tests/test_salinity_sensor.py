import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.salinity_sensor import SalinitySensorService


class TestSalinitySensorService(unittest.TestCase):
    def test_voltage_to_salinity_conversion(self):
        mock_adc = MagicMock()
        # Mock 1.65V -> Salinity 17.5 ppt
        mock_adc.read_filtered_voltage.return_value = 1.65
        service = SalinitySensorService(adc_service=mock_adc)
        self.assertAlmostEqual(service.read_salinity(), 17.5)

        # Mock 3.3V -> Salinity 35.0 ppt
        mock_adc.read_filtered_voltage.return_value = 3.3
        self.assertAlmostEqual(service.read_salinity(), 35.0)

    def test_salinity_clamping(self):
        mock_adc = MagicMock()
        # Voltage that would result in salinity > 50
        mock_adc.read_filtered_voltage.return_value = 6.0
        service = SalinitySensorService(adc_service=mock_adc)
        self.assertEqual(service.read_salinity(), 50.0)

        # Voltage that would result in salinity < 0
        mock_adc.read_filtered_voltage.return_value = -1.0
        service = SalinitySensorService(adc_service=mock_adc)
        self.assertEqual(service.read_salinity(), 0.0)

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
