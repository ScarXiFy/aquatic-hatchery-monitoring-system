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


def ensure_w1_kernel_modules():
    """Attempts to load 1-Wire kernel drivers on Linux/RPi if running on hardware."""
    if os.name == "posix" and os.path.exists("/sys/bus"):
        try:
            os.system("modprobe w1-gpio 2>/dev/null")
            os.system("modprobe w1-therm 2>/dev/null")
        except Exception:
            pass


def discover_w1_sensors() -> list[str]:
    """Scans for available 1-Wire DS18B20 devices on the system."""
    ensure_w1_kernel_modules()
    if not W1_BASE_DIR.exists():
        return []
    devices = []
    for p in W1_BASE_DIR.glob("28-*"):
        if (p / "w1_slave").exists():
            devices.append(p.name)
    return sorted(devices)


def resolve_main_tank_device_file(custom_device_id: str | None = None) -> Path:
    """
    Resolves the main tank DS18B20 w1_slave file path.
    1. If custom_device_id is provided, uses that.
    2. If MAIN_TANK_DEVICE_FILE exists, returns that.
    3. Auto-discovers any connected 28-* device not assigned to mixing tank.
    4. If only 1 device is found, returns that.
    5. Fallback: MAIN_TANK_DEVICE_FILE.
    """
    if custom_device_id:
        return W1_BASE_DIR / custom_device_id / "w1_slave"

    if MAIN_TANK_DEVICE_FILE.exists():
        return MAIN_TANK_DEVICE_FILE

    devices = discover_w1_sensors()
    other_devices = [d for d in devices if d != MIXING_TANK_DEVICE_ID]
    if other_devices:
        return W1_BASE_DIR / other_devices[0] / "w1_slave"
    if devices:
        return W1_BASE_DIR / devices[0] / "w1_slave"

    return MAIN_TANK_DEVICE_FILE


def resolve_mixing_tank_device_file(custom_device_id: str | None = None) -> Path:
    """
    Resolves the mixing tank DS18B20 w1_slave file path.
    1. If custom_device_id is provided, uses that.
    2. If MIXING_TANK_DEVICE_FILE exists, returns that.
    3. Auto-discovers any connected 28-* device not assigned to main tank.
    4. If only 1 device is found, returns that.
    5. Fallback: MIXING_TANK_DEVICE_FILE.
    """
    if custom_device_id:
        return W1_BASE_DIR / custom_device_id / "w1_slave"

    if MIXING_TANK_DEVICE_FILE.exists():
        return MIXING_TANK_DEVICE_FILE

    devices = discover_w1_sensors()
    other_devices = [d for d in devices if d != MAIN_TANK_DEVICE_ID]
    if other_devices:
        return W1_BASE_DIR / other_devices[0] / "w1_slave"
    if devices:
        return W1_BASE_DIR / devices[0] / "w1_slave"

    return MIXING_TANK_DEVICE_FILE


def is_w1_sensor_available(device_file: Path | str = MAIN_TANK_DEVICE_FILE) -> bool:
    """Checks whether the specified 1-Wire w1_slave file is present."""
    ensure_w1_kernel_modules()
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
        temp_c = round(temp_raw / 1000.0, 2)
        # DS18B20 power-on reset scratchpad register value (+85.0°C) is treated as invalid/unconverted
        if abs(temp_c - 85.0) < 0.01:
            return None
        return temp_c
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


def read_main_tank_temp(device_file: Path | str | None = None, device_id: str | None = None) -> float | None:
    """
    Reads the temperature (°C) of the Main Tank DS18B20 sensor.
    Supports overriding device file or ID, with auto-discovery fallback.
    """
    if device_file is not None:
        target_file = Path(device_file)
    elif device_id is not None:
        target_file = W1_BASE_DIR / device_id / "w1_slave"
    else:
        target_file = resolve_main_tank_device_file()

    return read_ds18b20(target_file)


def read_mixing_tank_temp(device_file: Path | str | None = None, device_id: str | None = None) -> float | None:
    """
    Reads the temperature (°C) of the Mixing Tank DS18B20 sensor.
    Supports overriding device file or ID, with auto-discovery fallback.
    """
    if device_file is not None:
        target_file = Path(device_file)
    elif device_id is not None:
        target_file = W1_BASE_DIR / device_id / "w1_slave"
    else:
        target_file = resolve_mixing_tank_device_file()

    return read_ds18b20(target_file)
