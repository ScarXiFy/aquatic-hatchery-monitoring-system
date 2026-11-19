import logging

try:
    from flask import current_app
except ImportError:
    current_app = None

from app.services.adc_service import ADCService

logger = logging.getLogger(__name__)


class PHSensorService:
    def __init__(self, adc_service=None):
        self.adc_service = adc_service or ADCService()

    def read_raw_voltage(self) -> float:
        """
        Reads filtered raw analog voltage from ADC Channel 0.
        Returns float voltage (V).
        """
        channel = 0
        try:
            if current_app:
                channel = current_app.config.get("PH_ADC_CHANNEL", 0)
        except (RuntimeError, AttributeError):
            pass
        return self.adc_service.read_filtered_voltage(channel)

    def _voltage_to_ph(self, voltage: float, slope: float = -6.41401, offset: float = 17.29167) -> float:
        """
        Converts analog voltage to pH using the linear equation:
        y = m * x + b
        where m is slope (-6.41401) and b is offset (17.29167).
        """
        return slope * voltage + offset

    def read_ph(self) -> float:
        """
        Reads filtered voltage from ADCService, converts it to pH using latest DB calibration
        (or configured constants fallback), clamps the result, and returns it. Returns None if reading fails.
        """
        try:
            # Default fallback parameters
            slope = -6.41401
            offset = 17.29167
            ph_min = 0.0
            ph_max = 14.0

            # Attempt to fetch most recent calibration from database
            try:
                from app.models import get_latest_calibration
                cal = get_latest_calibration("ph")
                if cal:
                    slope = cal["slope"]
                    offset = cal["offset"]
            except Exception:
                try:
                    if current_app:
                        slope = current_app.config.get("PH_SLOPE", -6.41401)
                        offset = current_app.config.get("PH_OFFSET", 17.29167)
                        ph_min = current_app.config.get("PH_MIN", 0.0)
                        ph_max = current_app.config.get("PH_MAX", 14.0)
                except (RuntimeError, AttributeError):
                    pass

            # Request filtered voltage from ADCService
            voltage = self.read_raw_voltage()

            # Compute pH: y = m * x + b
            ph = self._voltage_to_ph(voltage, slope=slope, offset=offset)

            # Clamp the final result between PH_MIN and PH_MAX
            ph_clamped = max(ph_min, min(ph_max, ph))
            print(f"[RPI] pH sensor reading - {ph_clamped} at voltage {voltage}V")
            return round(ph_clamped, 2)
        except Exception:
            logger.exception("Failed to read PH sensor")
            return None
