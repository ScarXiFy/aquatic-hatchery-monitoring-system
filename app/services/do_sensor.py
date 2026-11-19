import logging

try:
    from flask import current_app
except ImportError:
    current_app = None

from app.services.adc_service import ADCService

logger = logging.getLogger(__name__)


class DOSensorService:
    def __init__(self, adc_service=None):
        self.adc_service = adc_service or ADCService()

    def read_raw_voltage(self) -> float:
        """
        Reads filtered raw analog voltage from ADC Channel 2.
        Returns float voltage (V).
        """
        channel = 2
        try:
            if current_app:
                channel = current_app.config.get("DO_ADC_CHANNEL", 2)
        except (RuntimeError, AttributeError):
            pass
        return self.adc_service.read_filtered_voltage(channel)

    def _voltage_to_do(self, voltage: float, slope: float = 5.93086, offset: float = 0.3273368) -> float:
        """
        Converts analog voltage to Dissolved Oxygen (mg/L) using the linear equation:
        y = m * x + b
        where m is slope (5.93086) and b is offset (0.3273368).
        """
        return slope * voltage + offset

    def read_do(self) -> float:
        """
        Reads filtered voltage from ADCService Channel 2, converts it to Dissolved Oxygen
        using the latest DB calibration (or config fallback), clamps the result, and returns it.
        Returns None if reading fails.
        """
        try:
            slope = 5.93086
            offset = 0.3273368
            do_min = 0.0
            do_max = 20.0

            try:
                from app.models import get_latest_calibration
                cal = get_latest_calibration("do")
                if cal:
                    slope = cal["slope"]
                    offset = cal["offset"]
            except Exception:
                try:
                    if current_app:
                        slope = current_app.config.get("DO_SLOPE", 5.93086)
                        offset = current_app.config.get("DO_OFFSET", 0.3273368)
                        do_min = current_app.config.get("DO_MIN", 0.0)
                        do_max = current_app.config.get("DO_MAX", 20.0)
                except (RuntimeError, AttributeError):
                    pass

            voltage = self.read_raw_voltage()
            do_val = self._voltage_to_do(voltage, slope=slope, offset=offset)

            do_clamped = max(do_min, min(do_max, do_val))
            print(f"[RPI] Dissolved Oxygen sensor reading - {do_clamped} at voltage {voltage}V")
            return round(do_clamped, 2)
        except Exception:
            logger.exception("Failed to read Dissolved Oxygen sensor")
            return None


# Alias for flexibility
DissolvedOxygenSensorService = DOSensorService
