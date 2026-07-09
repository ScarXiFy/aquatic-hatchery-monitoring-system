import logging
from flask import current_app
from app.services.adc_service import ADCService

logger = logging.getLogger(__name__)


class PHSensorService:
    def __init__(self, adc_service=None):
        self.adc_service = adc_service or ADCService()

    def read_ph(self) -> float:
        """
        Reads filtered voltage from ADCService, converts it to pH using configured constants,
        clamps the result, and returns it. Returns None if reading fails.
        """
        try:
            # Load config parameters (with defaults just in case)
            channel = 0
            neutral_voltage = 2.50
            slope = 0.18
            ph_min = 0.0
            ph_max = 14.0

            try:
                if current_app:
                    channel = current_app.config.get("PH_ADC_CHANNEL", 0)
                    neutral_voltage = current_app.config.get("PH_NEUTRAL_VOLTAGE", 2.50)
                    slope = current_app.config.get("PH_SLOPE", 0.18)
                    ph_min = current_app.config.get("PH_MIN", 0.0)
                    ph_max = current_app.config.get("PH_MAX", 14.0)
            except RuntimeError:
                # Outside flask context (e.g. running unit tests)
                pass

            # Request filtered voltage from ADCService
            voltage = self.adc_service.read_filtered_voltage(channel)

            # Compute pH: ph = 7.0 + (PH_NEUTRAL_VOLTAGE - voltage) / PH_SLOPE
            ph = 7.0 + (neutral_voltage - voltage) / slope

            # Clamp the final result between PH_MIN and PH_MAX
            ph_clamped = max(ph_min, min(ph_max, ph))
            
            return round(ph_clamped, 2)
        except Exception:
            logger.exception("Failed to read PH4502C sensor")
            return None
