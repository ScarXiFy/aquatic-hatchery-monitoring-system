import logging
from flask import current_app
from app.services.adc_service import ADCService

logger = logging.getLogger(__name__)


class DOSensorService:
    def __init__(self, adc_service=None):
        self.adc_service = adc_service or ADCService()

    def _voltage_to_do(self, voltage: float) -> float:
        """
        Temporary placeholder formula converting analog voltage to Dissolved Oxygen (mg/L).
        Isolated so it can easily be replaced after calibration.
        Default mapping: 0V -> 0 mg/L, 3.3V -> 10.0 mg/L.
        """
        return (voltage / 3.3) * 10.0

    def read_do(self) -> float:
        """
        Reads filtered voltage from ADCService Channel 2, converts it to Dissolved Oxygen,
        clamps the result, and returns it. Returns None if reading fails.
        """
        try:
            channel = 2
            do_min = 0.0
            do_max = 20.0

            try:
                if current_app:
                    channel = current_app.config.get("DO_ADC_CHANNEL", 2)
                    do_min = current_app.config.get("DO_MIN", 0.0)
                    do_max = current_app.config.get("DO_MAX", 20.0)
            except RuntimeError:
                # Outside Flask context
                pass

            voltage = self.adc_service.read_filtered_voltage(channel)
            do_val = self._voltage_to_do(voltage)

            do_clamped = max(do_min, min(do_max, do_val))
            print(f"[RPI] Dissolved Oxygen sensor reading - {do_clamped} at voltage {voltage}V")
            return round(do_clamped, 2)
        except Exception:
            logger.exception("Failed to read Dissolved Oxygen sensor")
            return None


# Alias for flexibility
DissolvedOxygenSensorService = DOSensorService
