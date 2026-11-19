import logging

try:
    from flask import current_app
except ImportError:
    current_app = None

from app.services.adc_service import ADCService

logger = logging.getLogger(__name__)


class SalinitySensorService:
    def __init__(self, adc_service=None):
        self.adc_service = adc_service or ADCService()

    def read_raw_voltage(self) -> float:
        """
        Reads filtered raw analog voltage from ADC Channel 1.
        Returns float voltage (V).
        """
        channel = 1
        try:
            if current_app:
                channel = current_app.config.get("SALINITY_ADC_CHANNEL", 1)
        except (RuntimeError, AttributeError):
            pass
        return self.adc_service.read_filtered_voltage(channel)

    def _voltage_to_salinity(
        self,
        voltage: float,
        temp_c: float = 25.0,
        slope: float = 38.265306,
        offset: float = 0.0,
        v_0: float = 0.00255,
        alpha: float = 0.02,
    ) -> float:
        """
        Converts analog voltage to Salinity (ppt).
        If offset is non-zero, applies linear calibration: y = slope * voltage + offset.
        Otherwise applies temperature compensation with baseline v_0:
            live_v25 = ((live_v_raw - V_0) / (1.0 + alpha * (live_temp - 25.0))) + V_0
            y = m * (x - V_0)
        """
        if offset != 0.0:
            return slope * voltage + offset

        if temp_c is None:
            temp_c = 25.0
        denom = 1.0 + alpha * (temp_c - 25.0)
        if abs(denom) < 1e-6:
            denom = 1.0
        live_v25 = ((voltage - v_0) / denom) + v_0
        salinity = slope * (live_v25 - v_0)
        return salinity

    def read_salinity(self, temp_c: float = None) -> float:
        """
        Reads filtered voltage from ADCService Channel 1, applies calibration
        from the database (or config), clamps the result, and returns it.
        """
        try:
            slope = 38.265306
            offset = 0.0
            v_0 = 0.00255
            alpha = 0.02
            salinity_min = 0.0
            salinity_max = 50.0

            try:
                from app.models import get_latest_calibration
                cal = get_latest_calibration("salinity")
                if cal:
                    slope = cal["slope"]
                    offset = cal["offset"]
            except Exception:
                try:
                    if current_app:
                        slope = current_app.config.get("SALINITY_SLOPE", 38.265306)
                        offset = current_app.config.get("SALINITY_OFFSET", 0.0)
                        v_0 = current_app.config.get("SALINITY_V0", 0.00255)
                        alpha = current_app.config.get("SALINITY_ALPHA", 0.02)
                        salinity_min = current_app.config.get("SALINITY_MIN", 0.0)
                        salinity_max = current_app.config.get("SALINITY_MAX", 50.0)
                except (RuntimeError, AttributeError):
                    pass

            if temp_c is None:
                try:
                    from app.services.temp_sensor import read_main_tank_temp
                    temp_c = read_main_tank_temp()
                except Exception:
                    temp_c = None

            if temp_c is None:
                try:
                    from app.api.piUtils import read_temp_sensor
                    temp_c = read_temp_sensor()
                except Exception:
                    temp_c = None

            if temp_c is None:
                temp_c = 25.0

            voltage = self.read_raw_voltage()
            salinity = self._voltage_to_salinity(
                voltage, temp_c=temp_c, slope=slope, offset=offset, v_0=v_0, alpha=alpha
            )

            salinity_clamped = max(salinity_min, min(salinity_max, salinity))
            print(f"[RPI] Salinity sensor reading - {salinity_clamped} at voltage {voltage}V (temp={temp_c}°C)")
            return round(salinity_clamped, 2)
        except Exception:
            logger.exception("Failed to read Salinity sensor")
            return None
