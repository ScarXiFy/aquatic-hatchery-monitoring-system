from pathlib import Path


class Config:
    BASE_DIR = Path(__file__).resolve().parent.parent
    SECRET_KEY = "dev-secret-key"
    DATABASE = BASE_DIR / "hatchery.db"
    SENSOR_INTERVAL_SECONDS = 3

    # ADS1115 and pH sensor calibration config
    ADS1115_ADDRESS = 0x48
    PH_ADC_CHANNEL = 0
    PH_NEUTRAL_VOLTAGE = 2.50
    PH_SLOPE = 0.18
    PH_MIN = 0.0
    PH_MAX = 14.0
