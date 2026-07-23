import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.do_sensor import DOSensorService


class TestDOSensorService(unittest.TestCase):
    def test_voltage_to_do_conversion(self):
        mock_adc = MagicMock()
        # Mock 1.65V -> DO 5.0 mg/L
        mock_adc.read_filtered_voltage.return_value = 1.65
        service = DOSensorService(adc_service=mock_adc)
        self.assertAlmostEqual(service.read_do(), 5.0)

        # Mock 3.3V -> DO 10.0 mg/L
        mock_adc.read_filtered_voltage.return_value = 3.3
        self.assertAlmostEqual(service.read_do(), 10.0)

    def test_do_clamping(self):
        mock_adc = MagicMock()
        # Voltage that would result in DO > 20
        mock_adc.read_filtered_voltage.return_value = 10.0
        service = DOSensorService(adc_service=mock_adc)
        self.assertEqual(service.read_do(), 20.0)

        # Voltage that would result in DO < 0
        mock_adc.read_filtered_voltage.return_value = -1.0
        service = DOSensorService(adc_service=mock_adc)
        self.assertEqual(service.read_do(), 0.0)

    @patch("app.services.do_sensor.logger")
    def test_do_sensor_failure_handling(self, mock_logger):
        mock_adc = MagicMock()
        mock_adc.read_filtered_voltage.side_effect = RuntimeError("Hardware failure")
        service = DOSensorService(adc_service=mock_adc)

        # Should return None instead of crashing
        do_val = service.read_do()
        self.assertIsNone(do_val)
        # Should log exception
        mock_logger.exception.assert_called_with("Failed to read Dissolved Oxygen sensor")
