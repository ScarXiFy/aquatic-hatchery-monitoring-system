from pathlib import Path


class Config:
    BASE_DIR = Path(__file__).resolve().parent.parent
    SECRET_KEY = "dev-secret-key"
    DATABASE = BASE_DIR / "hatchery.db"
    SENSOR_INTERVAL_SECONDS = 3

    # ADS1115 and sensor calibration config
    ADS1115_ADDRESS = 0x48
    PH_ADC_CHANNEL = 0
    PH_NEUTRAL_VOLTAGE = 2.50
    PH_SLOPE = 0.18
    PH_MIN = 0.0
    PH_MAX = 14.0

    # Salinity sensor calibration config (Channel 1)
    SALINITY_ADC_CHANNEL = 1
    SALINITY_VOLTAGE_ZERO = 0.0
    SALINITY_VOLTAGE_MAX = 3.3
    SALINITY_MIN = 0.0
    SALINITY_MAX = 50.0

    # Dissolved Oxygen sensor calibration config (Channel 2)
    DO_ADC_CHANNEL = 2
    DO_VOLTAGE_ZERO = 0.0
    DO_VOLTAGE_MAX = 3.3
    DO_MIN = 0.0
    DO_MAX = 20.0

