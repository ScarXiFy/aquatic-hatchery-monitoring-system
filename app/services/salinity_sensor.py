import logging
from flask import current_app
from app.services.adc_service import ADCService

logger = logging.getLogger(__name__)


class SalinitySensorService:
    def __init__(self, adc_service=None):
        self.adc_service = adc_service or ADCService()

    def _voltage_to_salinity(
        self,
        voltage: float,
        temp_c: float = 25.0,
        slope: float = 38.265306,
        v_0: float = 0.00255,
        alpha: float = 0.02,
    ) -> float:
        """
        Converts analog voltage to Salinity (ppt) using temperature compensation
        and the calibrated equation:
            live_v25 = ((live_v_raw - V_0) / (1.0 + alpha * (live_temp - 25.0))) + V_0
            y = m * (x - V_0), where x is live_v25.
        """
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
        Reads filtered voltage from ADCService Channel 1, applies temperature
        compensation with main tank DS18B20 temperature, converts to Salinity,
        clamps the result, and returns it. Returns None if reading fails.
        """
        try:
            channel = 1
            slope = 38.265306
            v_0 = 0.00255
            alpha = 0.02
            salinity_min = 0.0
            salinity_max = 50.0

            try:
                if current_app:
                    channel = current_app.config.get("SALINITY_ADC_CHANNEL", 1)
                    slope = current_app.config.get("SALINITY_SLOPE", 38.265306)
                    v_0 = current_app.config.get("SALINITY_V0", 0.00255)
                    alpha = current_app.config.get("SALINITY_ALPHA", 0.02)
                    salinity_min = current_app.config.get("SALINITY_MIN", 0.0)
                    salinity_max = current_app.config.get("SALINITY_MAX", 50.0)
            except RuntimeError:
                # Outside Flask context
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

            voltage = self.adc_service.read_filtered_voltage(channel)
            salinity = self._voltage_to_salinity(voltage, temp_c=temp_c, slope=slope, v_0=v_0, alpha=alpha)

            salinity_clamped = max(salinity_min, min(salinity_max, salinity))
            print(f"[RPI] Salinity sensor reading - {salinity_clamped} at voltage {voltage}V (temp={temp_c}°C)")
            return round(salinity_clamped, 2)
        except Exception:
            logger.exception("Failed to read Salinity sensor")
            return None
