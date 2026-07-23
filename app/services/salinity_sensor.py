import logging
from flask import current_app
from app.services.adc_service import ADCService

logger = logging.getLogger(__name__)


class SalinitySensorService:
    def __init__(self, adc_service=None):
        self.adc_service = adc_service or ADCService()

    def _voltage_to_salinity(self, voltage: float) -> float:
        """
        Temporary placeholder formula converting analog voltage to Salinity (ppt).
        Isolated so it can easily be replaced after calibration.
        Default mapping: 0V -> 0 ppt, 3.3V -> 35.0 ppt.
        """
        return (voltage / 3.3) * 35.0

    def read_salinity(self) -> float:
        """
        Reads filtered voltage from ADCService Channel 1, converts it to Salinity,
        clamps the result, and returns it. Returns None if reading fails.
        """
        try:
            channel = 1
            salinity_min = 0.0
            salinity_max = 50.0

            try:
                if current_app:
                    channel = current_app.config.get("SALINITY_ADC_CHANNEL", 1)
                    salinity_min = current_app.config.get("SALINITY_MIN", 0.0)
                    salinity_max = current_app.config.get("SALINITY_MAX", 50.0)
            except RuntimeError:
                # Outside Flask context
                pass

            voltage = self.adc_service.read_filtered_voltage(channel)
            salinity = self._voltage_to_salinity(voltage)

            salinity_clamped = max(salinity_min, min(salinity_max, salinity))
            print(f"[RPI] Salinity sensor reading - {salinity_clamped} at voltage {voltage}V")
            return round(salinity_clamped, 2)
        except Exception:
            logger.exception("Failed to read Salinity sensor")
            return None
