from pathlib import Path


class Config:
    BASE_DIR = Path(__file__).resolve().parent.parent
    SECRET_KEY = "dev-secret-key"
    DATABASE = BASE_DIR / "hatchery.db"
    SENSOR_INTERVAL_SECONDS = 3

    # ADS1115 and sensor calibration config
    ADS1115_ADDRESS = 0x48

    # pH sensor calibration config (Channel 0)
    # Calibrated linear formula: y = m * x + b
    PH_ADC_CHANNEL = 0
    PH_SLOPE = -6.41401
    PH_OFFSET = 17.29167
    PH_MIN = 0.0
    PH_MAX = 14.0

    # Salinity sensor calibration config (Channel 1)
    # Calibrated formula with temperature compensation:
    # live_v25 = ((live_v_raw - V_0) / (1.0 + alpha * (live_temp - 25.0))) + V_0
    # y = m * (live_v25 - V_0)
    SALINITY_ADC_CHANNEL = 1
    SALINITY_SLOPE = 38.265306
    SALINITY_V0 = 0.00255
    SALINITY_ALPHA = 0.02  # Standard aquatic EC temp coefficient per deg C
    SALINITY_MIN = 0.0
    SALINITY_MAX = 50.0

    # Dissolved Oxygen sensor calibration config (Channel 2)
    # Calibrated linear formula: y = m * x + b
    DO_ADC_CHANNEL = 2
    DO_SLOPE = 5.93086
    DO_OFFSET = 0.3273368
    DO_MIN = 0.0
    DO_MAX = 20.0

