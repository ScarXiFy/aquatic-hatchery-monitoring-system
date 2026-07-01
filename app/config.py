from pathlib import Path


class Config:
    BASE_DIR = Path(__file__).resolve().parent.parent
    SECRET_KEY = "dev-secret-key"
    DATABASE = BASE_DIR / "hatchery.db"
    SENSOR_INTERVAL_SECONDS = 3
    TRENDS_MODE = "last_24h"
