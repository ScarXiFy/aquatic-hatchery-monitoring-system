import logging
from flask import current_app
from app.services.adc_service import ADCService

logger = logging.getLogger(__name__)


class DOSensorService:
    def __init__(self, adc_service=None):
        self.adc_service = adc_service or ADCService()

    def _voltage_to_do(self, voltage: float, slope: float = 5.93086, offset: float = 0.3273368) -> float:
        """
        Converts analog voltage to Dissolved Oxygen (mg/L) using the calibrated linear equation:
        y = m * x + b
        where m is slope (5.93086) and b is offset (0.3273368).
        """
        return slope * voltage + offset

    def read_do(self) -> float:
        """
        Reads filtered voltage from ADCService Channel 2, converts it to Dissolved Oxygen,
        clamps the result, and returns it. Returns None if reading fails.
        """
        try:
            channel = 2
            slope = 5.93086
            offset = 0.3273368
            do_min = 0.0
            do_max = 20.0

            try:
                if current_app:
                    channel = current_app.config.get("DO_ADC_CHANNEL", 2)
                    slope = current_app.config.get("DO_SLOPE", 5.93086)
                    offset = current_app.config.get("DO_OFFSET", 0.3273368)
                    do_min = current_app.config.get("DO_MIN", 0.0)
                    do_max = current_app.config.get("DO_MAX", 20.0)
            except RuntimeError:
                # Outside Flask context
                pass

            voltage = self.adc_service.read_filtered_voltage(channel)
            do_val = self._voltage_to_do(voltage, slope=slope, offset=offset)

            do_clamped = max(do_min, min(do_max, do_val))
            print(f"[RPI] Dissolved Oxygen sensor reading - {do_clamped} at voltage {voltage}V")
            return round(do_clamped, 2)
        except Exception:
            logger.exception("Failed to read Dissolved Oxygen sensor")
            return None


# Alias for flexibility
DissolvedOxygenSensorService = DOSensorService
