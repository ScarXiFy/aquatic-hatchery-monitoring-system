#!/usr/bin/env python3
"""
Dedicated Calibrated Single-Sensor Reader & Logger

Allows selecting and monitoring only one sensor at a time (pH, Dissolved Oxygen,
Salinity, Main Tank Temperature, or Mixing Tank Temperature), applies the calibrated
slope and offset equations to live analog voltage readings, logs the calibrated output
with timestamps into a dedicated SQLite database, and prints live readings and
statistical summaries to the terminal.

Calibrated Equations Applied:
  - pH:          y = (-6.41401) * voltage + 17.29167
  - DO:          y = 5.93086 * voltage + 0.3273368
  - Salinity:    live_v25 = ((voltage - 0.00255) / (1.0 + 0.02 * (live_temp - 25.0))) + 0.00255
                 y = 38.265306 * (live_v25 - 0.00255)
  - Temp:        Direct Main Tank DS18B20 digital read (accurate as-is, no calibration needed)
  - Mixing Temp: Direct Mixing Tank DS18B20 digital read (accurate as-is, no calibration needed)
"""

import argparse
import csv
import math
import os
import random
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Default Configuration
DEFAULT_DB_FILE = BASE_DIR / "calibrated_sensors.db"
DEFAULT_INTERVAL_SECONDS = 5

SENSOR_METADATA = {
    "ph": {
        "name": "pH Sensor",
        "unit": "pH",
        "default_channel": 0,
        "slope": -6.41401,
        "offset": 17.29167,
        "min": 0.0,
        "max": 14.0,
        "sim_voltage_range": (1.45, 1.65),  # ~6.7 - 8.0 pH
    },
    "do": {
        "name": "Dissolved Oxygen (DO)",
        "unit": "mg/L",
        "default_channel": 2,
        "slope": 5.93086,
        "offset": 0.3273368,
        "min": 0.0,
        "max": 20.0,
        "sim_voltage_range": (1.0, 1.3),  # ~6.2 - 8.0 mg/L
    },
    "salinity": {
        "name": "Salinity (EC)",
        "unit": "ppt",
        "default_channel": 1,
        "slope": 38.265306,
        "v_0": 0.00255,
        "alpha": 0.02,
        "min": 0.0,
        "max": 50.0,
        "sim_voltage_range": (0.75, 0.95),  # ~28.5 - 36.2 ppt
    },
    "temp": {
        "name": "Main Tank Temperature (DS18B20)",
        "unit": "°C",
        "min": 0.0,
        "max": 60.0,
        "sim_range": (25.5, 27.5),
    },
    "mixing_temp": {
        "name": "Mixing Tank Temperature (DS18B20)",
        "unit": "°C",
        "min": 0.0,
        "max": 60.0,
        "sim_range": (25.5, 27.5),
    },
}

# Sensor alias mapping
SENSOR_ALIASES = {
    "1": "ph",
    "ph": "ph",
    "ph_sensor": "ph",
    "2": "do",
    "do": "do",
    "dissolved_oxygen": "do",
    "3": "salinity",
    "salinity": "salinity",
    "ec": "salinity",
    "4": "temp",
    "temp": "temp",
    "temperature": "temp",
    "main_temp": "temp",
    "temp_main": "temp",
    "main_tank_temp": "temp",
    "5": "mixing_temp",
    "mixing_temp": "mixing_temp",
    "temp_mixing": "mixing_temp",
    "mixing": "mixing_temp",
    "mixing_tank_temp": "mixing_temp",
    "temp_mixing_tank": "mixing_temp",
    "mixing_ds18b20": "mixing_temp",
    "ds18b20_mixing": "mixing_temp",
}


class CalibratedSensorReader:
    def __init__(
        self,
        sensor_key: str,
        db_path: Path = DEFAULT_DB_FILE,
        interval: int = DEFAULT_INTERVAL_SECONDS,
        channel: int | None = None,
        device_id: str | None = None,
        notes: str | None = None,
        simulate: bool = False,
        reset: bool = False,
    ):
        self.sensor_key = self._normalize_sensor_key(sensor_key)
        self.meta = SENSOR_METADATA[self.sensor_key]
        self.db_path = Path(db_path)
        self.interval = interval
        self.channel = channel if channel is not None else self.meta.get("default_channel", 0)
        self.device_id = device_id
        self.device_file = None
        self.notes = notes
        self.simulate = simulate
        self.reset = reset

        self.conn = None
        self.adc_service = None
        self.is_hardware_available = False

        self.session_raw_readings = []
        self.session_cal_readings = []
        self.running = True

        self._init_db()
        self._init_hardware()

    @staticmethod
    def _normalize_sensor_key(key: str) -> str:
        k = str(key).strip().lower()
        if k in SENSOR_ALIASES:
            return SENSOR_ALIASES[k]
        raise ValueError(f"Unknown sensor '{key}'. Available options: ph, do, salinity, temp, mixing_temp")

    def _init_db(self):
        """Initializes the dedicated SQLite database and table."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            if self.reset:
                self.conn.execute("DROP TABLE IF EXISTS calibrated_sensor_readings")
                print(f"[INFO] Cleared previous readings from {self.db_path.name} (--reset).")

            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS calibrated_sensor_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    sensor TEXT NOT NULL,
                    raw_value REAL NOT NULL,
                    calibrated_value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    temp_compensated REAL,
                    notes TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cal_readings_ts_sensor
                ON calibrated_sensor_readings (sensor, timestamp)
                """
            )

    def _init_hardware(self):
        """Initializes ADC or 1-Wire hardware or switches to simulation mode."""
        if self.simulate:
            print("[INFO] Running in SIMULATION mode as requested by flag.")
            self.is_hardware_available = False
            return

        if self.sensor_key in ("temp", "mixing_temp"):
            try:
                from app.services.temp_sensor import (
                    MAIN_TANK_DEVICE_FILE,
                    MIXING_TANK_DEVICE_FILE,
                    discover_w1_sensors,
                    ensure_w1_kernel_modules,
                    is_w1_sensor_available,
                    resolve_main_tank_device_file,
                    resolve_mixing_tank_device_file,
                )

                ensure_w1_kernel_modules()
                detected_devices = discover_w1_sensors()

                if self.sensor_key == "mixing_temp":
                    name = "Mixing Tank DS18B20"
                    expected_default = MIXING_TANK_DEVICE_FILE
                    target_file = resolve_mixing_tank_device_file(self.device_id)
                else:
                    name = "Main Tank DS18B20"
                    expected_default = MAIN_TANK_DEVICE_FILE
                    target_file = resolve_main_tank_device_file(self.device_id)

                self.device_file = target_file

                if target_file and is_w1_sensor_available(target_file):
                    self.is_hardware_available = True
                    discovered_note = ""
                    if self.device_id:
                        discovered_note = f" (specified: {self.device_id})"
                    elif target_file != expected_default:
                        discovered_note = f" (auto-discovered on 1-Wire bus: {target_file.parent.name})"
                    else:
                        discovered_note = f" ({target_file.parent.name})"

                    print(f"[HARDWARE] {name} Temperature sensor detected{discovered_note}.")
                    return

                self.is_hardware_available = False
                print(f"[WARNING] {name} sensor file not found: {target_file}")
                if detected_devices:
                    print(f"[INFO] 1-Wire devices currently online on bus: {', '.join(detected_devices)}")
                    print(f"[TIP] To bind directly to one of these, use: --device-id {detected_devices[0]}")
                else:
                    print("[WARNING] No 1-Wire devices detected under /sys/bus/w1/devices/.")
                    print("[TIP] Ensure 1-Wire is enabled (`sudo raspi-config` -> Interfaces -> 1-Wire) and check the 4.7kΩ pull-up resistor on GPIO 4.")
                print(f"[WARNING] Falling back to simulated {name} readings.")
            except Exception as e:
                self.is_hardware_available = False
                print(f"[WARNING] Temperature sensor init failed ({e}). Falling back to simulation.")
            return

        try:
            from app.services.adc_service import ADCService

            self.adc_service = ADCService()
            if self.adc_service.available:
                self.is_hardware_available = True
                print(f"[HARDWARE] ADS1115 ADC detected. Monitoring channel AIN{self.channel} ({self.meta['name']}).")
            else:
                self.is_hardware_available = False
                print(f"[WARNING] ADS1115 ADC not detected. Falling back to simulated {self.meta['name']} readings.")
        except Exception as e:
            self.is_hardware_available = False
            print(f"[WARNING] ADCService init failed ({e}). Falling back to simulation.")

    def read_live_raw(self) -> float:
        """Reads raw voltage from ADC, or temperature from DS18B20."""
        if self.sensor_key in ("temp", "mixing_temp"):
            if self.is_hardware_available and getattr(self, "device_file", None):
                try:
                    from app.services.temp_sensor import read_ds18b20
                    val = read_ds18b20(self.device_file)
                    if val is not None:
                        return round(val, 2)
                    print(f"[WARNING] Transient read/CRC retry timeout on {self.device_file.parent.name}; retrying...")
                except Exception as e:
                    print(f"[ERROR] Failed reading temperature sensor: {e}")
            sim_low, sim_high = self.meta["sim_range"]
            return round(random.uniform(sim_low, sim_high), 2)

        if self.is_hardware_available and self.adc_service:
            try:
                voltage = self.adc_service.read_filtered_voltage(self.channel)
                return round(voltage, 4)
            except Exception as e:
                print(f"[ERROR] Failed reading ADC channel {self.channel}: {e}. Retrying fallback.")

        sim_low, sim_high = self.meta["sim_voltage_range"]
        return round(random.uniform(sim_low, sim_high), 4)

    def get_live_temp_for_compensation(self) -> float:
        """Retrieves the live main tank DS18B20 temperature for salinity compensation."""
        try:
            from app.services.temp_sensor import read_main_tank_temp
            t = read_main_tank_temp()
            if t is not None:
                return round(t, 2)
        except Exception:
            pass
        return 25.0

    def calculate_calibrated_value(self, raw_val: float) -> tuple[float, float | None]:
        """
        Applies calibrated equations to convert raw voltage to parameter value.
        Returns (calibrated_value, temp_compensated).
        """
        if self.sensor_key == "ph":
            m = self.meta["slope"]
            b = self.meta["offset"]
            val = m * raw_val + b
            val = max(self.meta["min"], min(self.meta["max"], val))
            return round(val, 2), None

        if self.sensor_key == "do":
            m = self.meta["slope"]
            b = self.meta["offset"]
            val = m * raw_val + b
            val = max(self.meta["min"], min(self.meta["max"], val))
            return round(val, 2), None

        if self.sensor_key == "salinity":
            m = self.meta["slope"]
            v_0 = self.meta["v_0"]
            alpha = self.meta["alpha"]
            live_temp = self.get_live_temp_for_compensation()

            denom = 1.0 + alpha * (live_temp - 25.0)
            if abs(denom) < 1e-6:
                denom = 1.0
            live_v25 = ((raw_val - v_0) / denom) + v_0
            val = m * (live_v25 - v_0)
            val = max(self.meta["min"], min(self.meta["max"], val))
            return round(val, 2), live_temp

        if self.sensor_key in ("temp", "mixing_temp"):
            return round(raw_val, 2), None

        return round(raw_val, 2), None

    def record_reading(
        self,
        raw_val: float,
        cal_val: float,
        temp_comp: float | None = None,
        timestamp: str | None = None,
    ) -> int:
        """Stores the raw and calibrated readings into the SQLite database."""
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO calibrated_sensor_readings (
                timestamp, sensor, raw_value, calibrated_value, unit, temp_compensated, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                self.sensor_key,
                raw_val,
                cal_val,
                self.meta["unit"],
                temp_comp,
                self.notes,
            ),
        )
        self.conn.commit()
        reading_id = cursor.lastrowid
        self.session_raw_readings.append(raw_val)
        self.session_cal_readings.append(cal_val)
        return reading_id

    def display_reading(
        self,
        reading_id: int,
        timestamp: str,
        raw_val: float,
        cal_val: float,
        temp_comp: float | None = None,
    ):
        """Displays formatted reading in the terminal."""
        sample_num = len(self.session_cal_readings)
        mode_tag = "[HARDWARE]" if self.is_hardware_available else "[SIMULATED]"
        unit = self.meta["unit"]

        if self.sensor_key == "salinity":
            t_str = f" | Live Temp (DS18B20): {temp_comp:.2f}°C" if temp_comp is not None else ""
            print(
                f"{mode_tag} #{sample_num:03d} | {timestamp} | "
                f"Raw Voltage: {raw_val:.4f} V | Calibrated Salinity: {cal_val:5.2f} {unit}{t_str} | "
                f"DB ID: {reading_id}"
            )
        elif self.sensor_key in ("temp", "mixing_temp"):
            f_val = (cal_val * 9.0 / 5.0) + 32.0
            print(
                f"{mode_tag} #{sample_num:03d} | {timestamp} | "
                f"Reading: {cal_val:5.2f} {unit} ({f_val:5.2f}°F) | "
                f"DB ID: {reading_id}"
            )
        else:
            print(
                f"{mode_tag} #{sample_num:03d} | {timestamp} | "
                f"Raw Voltage: {raw_val:.4f} V | Calibrated {self.meta['name']}: {cal_val:5.2f} {unit} | "
                f"DB ID: {reading_id}"
            )

    def print_summary(self):
        """Prints a statistical summary of the session upon termination."""
        count = len(self.session_cal_readings)
        unit = self.meta["unit"]
        print("\n" + "=" * 75)
        print(f"       CALIBRATED {self.meta['name'].upper()} MONITORING SUMMARY")
        print("=" * 75)
        print(f"Sensor Target           : {self.meta['name']} ({self.sensor_key})")
        print(f"Total Readings Recorded : {count}")
        print(f"Sampling Interval       : {self.interval} seconds")
        print(f"Dedicated Database File : {self.db_path.resolve()}")
        if self.notes:
            print(f"Session Note            : {self.notes}")

        if count > 0:
            avg_val = sum(self.session_cal_readings) / count
            min_val = min(self.session_cal_readings)
            max_val = max(self.session_cal_readings)
            spread = max_val - min_val

            variance = sum((x - avg_val) ** 2 for x in self.session_cal_readings) / count
            std_dev = math.sqrt(variance)

            print("-" * 75)
            print(f"Average Calibrated Output: {avg_val:.2f} {unit}")
            print(f"Minimum Calibrated Output: {min_val:.2f} {unit}")
            print(f"Maximum Calibrated Output: {max_val:.2f} {unit}")
            print(f"Output Spread (Max - Min): {spread:.2f} {unit}")
            print(f"Standard Deviation       : {std_dev:.4f} {unit}")

            if self.sensor_key not in ("temp", "mixing_temp"):
                avg_raw = sum(self.session_raw_readings) / count
                print(f"Average Raw Voltage      : {avg_raw:.4f} V")
        print("=" * 75)

    def close(self):
        """Closes the SQLite database connection safely."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def _wait_interval(self):
        """Waits for self.interval seconds, checking every 0.25s for responsive Ctrl+C exit."""
        start_time = time.time()
        while time.time() - start_time < self.interval:
            if not self.running:
                break
            time.sleep(0.25)

    def run(self):
        """Main loop that collects, calibrates, displays, and records readings."""
        mode = "REAL HARDWARE" if self.is_hardware_available else "SIMULATION"
        print("=" * 75)
        print(f"          CALIBRATED {self.meta['name'].upper()} MONITOR")
        print("=" * 75)
        print(f"Sensor        : {self.meta['name']} (Key: {self.sensor_key})")
        if self.sensor_key in ("temp", "mixing_temp"):
            dev_str = getattr(self, "device_file", None)
            dev_name = dev_str.parent.name if dev_str else "N/A"
            print(f"1-Wire Device : {dev_name}")
        else:
            print(f"ADC Channel   : AIN{self.channel}")
            if self.sensor_key == "ph":
                print(f"Calibration   : y = {self.meta['slope']} * V + {self.meta['offset']}")
            elif self.sensor_key == "do":
                print(f"Calibration   : y = {self.meta['slope']} * V + {self.meta['offset']}")
            elif self.sensor_key == "salinity":
                print(f"Calibration   : y = {self.meta['slope']} * (live_v25 - {self.meta['v_0']}) [alpha={self.meta['alpha']}]")
        print(f"Database File : {self.db_path}")
        print(f"Interval      : {self.interval} seconds")
        print(f"Hardware Mode : {mode}")
        if self.notes:
            print(f"Session Note  : {self.notes}")
        print("-" * 75)
        print("Press Ctrl+C at any time to finish and view summary.")
        print("-" * 75)

        def handle_signal(sig, frame):
            self.running = False

        try:
            signal.signal(signal.SIGTERM, handle_signal)
            signal.signal(signal.SIGINT, handle_signal)
        except (ValueError, AttributeError):
            pass

        try:
            while self.running:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                raw_val = self.read_live_raw()
                cal_val, temp_comp = self.calculate_calibrated_value(raw_val)

                reading_id = self.record_reading(raw_val, cal_val, temp_comp, timestamp)
                self.display_reading(reading_id, timestamp, raw_val, cal_val, temp_comp)

                self._wait_interval()
        except KeyboardInterrupt:
            print("\n[INFO] Monitoring stopped by user.")
        finally:
            self.print_summary()
            self.close()


def view_history(db_path: Path, sensor: str | None = None, limit: int = 50):
    """Displays recent calibrated records stored in the SQLite database."""
    if not db_path.exists():
        print(f"No database found at: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = "SELECT id, timestamp, sensor, raw_value, calibrated_value, unit, temp_compensated, notes FROM calibrated_sensor_readings"
    params = []
    if sensor:
        sensor_key = SENSOR_ALIASES.get(sensor.lower(), sensor.lower())
        query += " WHERE sensor = ?"
        params.append(sensor_key)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        filter_str = f" for sensor '{sensor}'" if sensor else ""
        print(f"Database exists but contains no calibrated readings{filter_str}.")
        return

    print("=" * 85)
    print(f"   RECENT CALIBRATED READINGS (Last {len(rows)} from {db_path.name})")
    print("=" * 85)
    print(f"{'ID':<6} | {'Timestamp':<19} | {'Sensor':<10} | {'Raw':<10} | {'Calibrated Output':<20} | Notes")
    print("-" * 85)
    for row in reversed(rows):
        notes = row["notes"] or ""
        cal_str = f"{row['calibrated_value']:.2f} {row['unit']}"
        raw_str = f"{row['raw_value']:.4f}V" if row["sensor"] not in ("temp", "mixing_temp") else f"{row['raw_value']:.2f}°C"
        print(f"{row['id']:<6} | {row['timestamp']:<19} | {row['sensor']:<10} | {raw_str:<10} | {cal_str:<20} | {notes}")
    print("=" * 85)


def export_csv(db_path: Path, output_file: Path, sensor: str | None = None):
    """Exports calibrated sensor readings to a CSV file."""
    if not db_path.exists():
        print(f"Error: Database {db_path} does not exist.")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = "SELECT id, timestamp, sensor, raw_value, calibrated_value, unit, temp_compensated, notes FROM calibrated_sensor_readings"
    params = []
    if sensor:
        sensor_key = SENSOR_ALIASES.get(sensor.lower(), sensor.lower())
        query += " WHERE sensor = ?"
        params.append(sensor_key)
    query += " ORDER BY id ASC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    output_path = Path(output_file)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Timestamp", "Sensor", "Raw Value", "Calibrated Value", "Unit", "Temp Compensated (°C)", "Notes"])
        for row in rows:
            writer.writerow([
                row["id"],
                row["timestamp"],
                row["sensor"],
                row["raw_value"],
                row["calibrated_value"],
                row["unit"],
                row["temp_compensated"] if row["temp_compensated"] is not None else "",
                row["notes"] or "",
            ])

    print(f"Exported {len(rows)} calibrated readings to {output_path.resolve()}")


def interactive_select_sensor() -> str:
    """Prompts the user to interactively choose a sensor to monitor."""
    detected = []
    main_status = ""
    mixing_status = ""
    try:
        from app.services.temp_sensor import (
            MAIN_TANK_DEVICE_FILE,
            MIXING_TANK_DEVICE_FILE,
            discover_w1_sensors,
            ensure_w1_kernel_modules,
        )
        ensure_w1_kernel_modules()
        detected = discover_w1_sensors()
        if MAIN_TANK_DEVICE_FILE.exists():
            main_status = f" [ONLINE: {MAIN_TANK_DEVICE_FILE.parent.name}]"
        elif detected:
            main_status = f" [DETECTED BUS SENSOR: {detected[0]}]"
        else:
            main_status = " [CONFIGURED]"

        if MIXING_TANK_DEVICE_FILE.exists():
            mixing_status = f" [ONLINE: {MIXING_TANK_DEVICE_FILE.parent.name}]"
        else:
            mix_cand = [d for d in detected if d != MAIN_TANK_DEVICE_FILE.parent.name]
            if mix_cand:
                mixing_status = f" [AUTO-RESOLVE: {mix_cand[0]}]"
            elif detected:
                mixing_status = f" [AUTO-RESOLVE: {detected[0]}]"
            else:
                mixing_status = " [CONFIGURED]"
    except Exception:
        pass

    print("\n" + "=" * 65)
    print("       AQUATIC HATCHERY SENSOR MONITOR")
    print("=" * 65)
    print("Select a sensor to monitor:")
    print("  1) pH Sensor (Channel 0)")
    print("  2) Dissolved Oxygen [DO] (Channel 2)")
    print("  3) Salinity / EC (Channel 1, Temp Compensated)")
    print(f"  4) Temperature (Main Tank DS18B20){main_status}")
    print(f"  5) Temperature (Mixing Tank DS18B20){mixing_status}")
    if detected:
        print(f"  --> 1-Wire device(s) online: {', '.join(detected)}")
    print("=" * 65)
    while True:
        try:
            choice = input("Enter choice [1-5 or name] (default: 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            sys.exit(0)

        if not choice:
            return "ph"
        if choice in SENSOR_ALIASES:
            return SENSOR_ALIASES[choice]
        print(f"Invalid selection '{choice}'. Please enter 1, 2, 3, 4, 5, or sensor name (ph, do, salinity, temp, mixing_temp).")


def main():
    parser = argparse.ArgumentParser(
        description="Calibrated Single-Sensor Reader & Logger: Reads from a selected sensor, applies calibration slopes and offsets, displays live values on the terminal, and logs to a dedicated SQLite database."
    )
    parser.add_argument(
        "--sensor",
        type=str,
        default=None,
        help="Sensor to read: 'ph', 'do', 'salinity', 'temp', or 'mixing_temp' (if omitted, prompts interactively)",
    )
    parser.add_argument(
        "--device-id",
        "-d",
        type=str,
        default=None,
        help="Optional 1-Wire DS18B20 device ID (e.g., '28-000000b260f4') override for temperature monitoring",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Sampling interval in seconds (default: {DEFAULT_INTERVAL_SECONDS}s)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_FILE,
        help=f"SQLite database file path (default: {DEFAULT_DB_FILE.name})",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=None,
        help="Optional ADC channel override (defaults to standard sensor channel)",
    )
    parser.add_argument(
        "--notes",
        type=str,
        default=None,
        help="Optional label or note for this reading session",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Force simulation mode without connecting to real hardware",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear previous readings in the database before starting",
    )
    parser.add_argument(
        "--view",
        action="store_true",
        help="View recent calibrated readings stored in the database and exit",
    )
    parser.add_argument(
        "--export-csv",
        type=Path,
        default=None,
        help="Export all calibrated readings to a CSV file and exit",
    )

    args = parser.parse_args()

    if args.view:
        view_history(args.db, sensor=args.sensor)
        return

    if args.export_csv:
        export_csv(args.db, args.export_csv, sensor=args.sensor)
        return

    sensor_choice = args.sensor
    if not sensor_choice:
        sensor_choice = interactive_select_sensor()

    reader = CalibratedSensorReader(
        sensor_key=sensor_choice,
        db_path=args.db,
        interval=args.interval,
        channel=args.channel,
        device_id=args.device_id,
        notes=args.notes,
        simulate=args.simulate,
        reset=args.reset,
    )
    reader.run()


if __name__ == "__main__":
    main()
