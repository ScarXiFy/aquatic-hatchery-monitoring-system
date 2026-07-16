import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import logging

# Ensure imports work from project root
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.adc_service import ADCService
from app.services.ph_sensor import PHSensorService
from app.sensors.simulator import generate_sensor_reading


class TestADCService(unittest.TestCase):
    @patch("app.services.adc_service.CIRCUITPYTHON_AVAILABLE", True)
    @patch("app.services.adc_service.busio", create=True)
    @patch("app.services.adc_service.board", create=True)
    @patch("app.services.adc_service.ADS", create=True)
    @patch("app.services.adc_service.AnalogIn", create=True)
    def test_adc_filtering(self, mock_analog_in, mock_ads, mock_board, mock_busio):
        # Reset the singleton instance for testing
        ADCService._instance = None
        service = ADCService()
        
        # Setup mock channel to return 10 specific voltage values
        mock_chan = MagicMock()
        type(mock_chan).voltage = PropertyMock(side_effect=[1.0, 1.9, 1.2, 1.8, 1.3, 1.7, 1.4, 1.6, 1.5, 1.1])
        
        service.channels[0] = mock_chan
        service.available = True
        
        voltage = service.read_filtered_voltage(0)
        self.assertAlmostEqual(voltage, 1.45)

    @patch("app.services.adc_service.CIRCUITPYTHON_AVAILABLE", True)
    @patch("app.services.adc_service.busio", create=True)
    @patch("app.services.adc_service.board", create=True)
    @patch("app.services.adc_service.ADS", create=True)
    @patch("app.services.adc_service.AnalogIn", create=True)
    @patch("app.services.adc_service.logger")
    def test_adc_i2c_failure(self, mock_logger, mock_analog_in, mock_ads, mock_board, mock_busio):
        ADCService._instance = None
        service = ADCService()
        
        # Mock a channel that raises an OSError on voltage access
        mock_chan = MagicMock()
        type(mock_chan).voltage = PropertyMock(side_effect=OSError("I2C bus error"))
        service.channels[0] = mock_chan
        service.available = True
        
        with self.assertRaises(OSError):
            service.read_filtered_voltage(0)
            
        self.assertFalse(service.available)
        mock_logger.exception.assert_called()


class TestPHSensorService(unittest.TestCase):
    def test_voltage_to_ph_conversion(self):
        mock_adc = MagicMock()
        # Mock 2.5V -> pH 7.0
        mock_adc.read_filtered_voltage.return_value = 2.5
        service = PHSensorService(adc_service=mock_adc)
        self.assertEqual(service.read_ph(), 7.0)

        # Mock 2.14V -> pH 9.0 (7 + (2.5 - 2.14) / 0.18 = 7 + 0.36 / 0.18 = 9.0)
        mock_adc.read_filtered_voltage.return_value = 2.14
        self.assertEqual(service.read_ph(), 9.0)

    def test_ph_clamping(self):
        mock_adc = MagicMock()
        # Voltage that would result in pH > 14
        mock_adc.read_filtered_voltage.return_value = 0.5
        service = PHSensorService(adc_service=mock_adc)
        self.assertEqual(service.read_ph(), 14.0)

        # Voltage that would result in pH < 0
        mock_adc.read_filtered_voltage.return_value = 4.5
        service = PHSensorService(adc_service=mock_adc)
        self.assertEqual(service.read_ph(), 0.0)

    @patch("app.services.ph_sensor.logger")
    def test_ph_sensor_failure_handling(self, mock_logger):
        mock_adc = MagicMock()
        mock_adc.read_filtered_voltage.side_effect = RuntimeError("Hardware failure")
        service = PHSensorService(adc_service=mock_adc)
        
        # Should return None instead of crashing
        ph = service.read_ph()
        self.assertIsNone(ph)
        # Should log exception
        mock_logger.exception.assert_called_with("Failed to read PH4502C sensor")


class TestSimulatorIntegration(unittest.TestCase):
    @patch("app.sensors.simulator.isRpiPresent", False)
    @patch("app.services.ph_sensor.PHSensorService")
    @patch("app.sensors.simulator.get_latest_reading")
    @patch("app.sensors.simulator.get_control_settings")
    @patch("app.sensors.simulator.read_temp_sensor")
    def test_rpi_disconnected_simulator(self, mock_temp, mock_settings, mock_latest, mock_ph_service_class):
        mock_settings.return_value = {"temperature_setpoint": 26.0, "dissolved_oxygen_setpoint": 7.2}
        mock_latest.return_value = {"temperature": 26.0, "dissolved_oxygen": 7.2}
        mock_temp.return_value = None
        
        # Reset mock
        mock_ph_service_class.reset_mock()

        # When RPi is disconnected, PHSensorService should never be initialized or called
        reading = generate_sensor_reading()
        
        mock_ph_service_class.assert_not_called()
        self.assertTrue(7.6 <= reading["ph"] <= 8.3)

    @patch("app.sensors.simulator.isRpiPresent", True)
    @patch("app.services.ph_sensor.PHSensorService")
    @patch("app.sensors.simulator.get_latest_reading")
    @patch("app.sensors.simulator.get_control_settings")
    @patch("app.sensors.simulator.read_temp_sensor")
    def test_rpi_connected_sensor_available(self, mock_temp, mock_settings, mock_latest, mock_ph_service_class):
        mock_settings.return_value = {"temperature_setpoint": 26.0, "dissolved_oxygen_setpoint": 7.2}
        mock_latest.return_value = {"temperature": 26.0, "dissolved_oxygen": 7.2}
        mock_temp.return_value = None
        
        # Mock service returning a valid physical reading
        mock_ph_service = MagicMock()
        mock_ph_service.read_ph.return_value = 8.12
        mock_ph_service_class.return_value = mock_ph_service
        
        reading = generate_sensor_reading()
        self.assertEqual(reading["ph"], 8.12)
        mock_ph_service.read_ph.assert_called_once()

    @patch("app.sensors.simulator.isRpiPresent", True)
    @patch("app.services.ph_sensor.PHSensorService")
    @patch("app.sensors.simulator.get_latest_reading")
    @patch("app.sensors.simulator.get_control_settings")
    @patch("app.sensors.simulator.read_temp_sensor")
    def test_rpi_connected_sensor_failed(self, mock_temp, mock_settings, mock_latest, mock_ph_service_class):
        mock_settings.return_value = {"temperature_setpoint": 26.0, "dissolved_oxygen_setpoint": 7.2}
        mock_latest.return_value = {"temperature": 26.0, "dissolved_oxygen": 7.2}
        mock_temp.return_value = None
        
        # Mock service returning None on hardware failure
        mock_ph_service = MagicMock()
        mock_ph_service.read_ph.return_value = None
        mock_ph_service_class.return_value = mock_ph_service
        
        reading = generate_sensor_reading()
        self.assertTrue(7.6 <= reading["ph"] <= 8.3)
        mock_ph_service.read_ph.assert_called_once()
