"""
DS18B20 1-Wire Temperature Sensor Service

Reads digital temperature readings directly from Linux 1-Wire virtual devices
(/sys/bus/w1/devices/28-*/w1_slave).
Operates strictly at the Linux kernel filesystem / 1-Wire bus level without
touching RPi.GPIO pins, relays, valves, or actuators.
"""

import os
import time
from pathlib import Path

W1_BASE_DIR = Path("/sys/bus/w1/devices")
MAIN_TANK_DEVICE_ID = "28-000000b197bd"
MIXING_TANK_DEVICE_ID = "28-000000b260f4"

MAIN_TANK_DEVICE_FILE = W1_BASE_DIR / MAIN_TANK_DEVICE_ID / "w1_slave"
MIXING_TANK_DEVICE_FILE = W1_BASE_DIR / MIXING_TANK_DEVICE_ID / "w1_slave"


def is_w1_sensor_available(device_file: Path | str = MAIN_TANK_DEVICE_FILE) -> bool:
    """Checks whether the specified 1-Wire w1_slave file is present."""
    return Path(device_file).exists()


def parse_w1_slave(lines: list[str]) -> float | None:
    """
    Parses output from a DS18B20 w1_slave file.
    Validates the CRC check line ('YES') and extracts the temperature in Celsius.
    """
    if not lines or len(lines) < 2:
        return None

    # Line 1: CRC status check
    if not lines[0].strip().endswith("YES"):
        return None

    # Line 2: Contains t=XXXXX
    equals_pos = lines[1].find("t=")
    if equals_pos == -1:
        return None

    try:
        temp_raw = float(lines[1][equals_pos + 2:].strip())
        return round(temp_raw / 1000.0, 2)
    except (ValueError, IndexError):
        return None


def read_ds18b20(device_file: Path | str, retries: int = 5, retry_delay: float = 0.2) -> float | None:
    """
    Reads temperature (°C) directly from the given w1_slave device file.
    Retries up to `retries` times on transient read or CRC errors.
    Returns float temperature or None if sensor is not available or read fails.
    """
    file_path = Path(device_file)
    if not file_path.exists():
        return None

    for _ in range(retries):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            temp_c = parse_w1_slave(lines)
            if temp_c is not None:
                return temp_c
        except Exception:
            pass
        time.sleep(retry_delay)

    return None


def read_main_tank_temp() -> float | None:
    """
    Reads the temperature (°C) of the Main Tank DS18B20 sensor (28-000000b197bd).
    Returns float or None if hardware is unavailable.
    """
    return read_ds18b20(MAIN_TANK_DEVICE_FILE)


def read_mixing_tank_temp() -> float | None:
    """
    Reads the temperature (°C) of the Mixing Tank DS18B20 sensor (28-000000b260f4).
    Returns float or None if hardware is unavailable.
    """
    return read_ds18b20(MIXING_TANK_DEVICE_FILE)
