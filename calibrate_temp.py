#!/usr/bin/env python3
"""
DS18B20 Temperature Sensor Calibration & Temperature Logger

Reads temperature from the selected DS18B20 1-Wire sensor (Main Tank or Mixing Tank)
every 20 seconds, displays the readings in the terminal, and records each reading with
a timestamp into a dedicated SQLite database (temp_calibration.db).
"""

import argparse
import csv
import glob
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
DEFAULT_DB_FILE = BASE_DIR / "temp_calibration.db"
DEFAULT_INTERVAL_SECONDS = 20

# Known sensor configurations
SENSOR_CONFIGS = {
    "main": {
        "key": "main",
        "label": "Main Tank Sensor",
        "default_device_id": "28-000000b197bd",
    },
    "mixing": {
        "key": "mixing",
        "label": "Mixing Tank Sensor",
        "default_device_id": "28-000000b260f4",
    },
}

W1_BASE_DIR = Path("/sys/bus/w1/devices")


def discover_w1_sensors():
    """Scans for available 1-Wire DS18B20 devices on the system."""
    if not W1_BASE_DIR.exists():
        return []
    devices = []
    for p in W1_BASE_DIR.glob("28-*"):
        if (p / "w1_slave").exists():
            devices.append(p.name)
    return devices


def parse_w1_slave_data(lines: list[str]) -> tuple[float | None, str]:
    """
    Parses lines from a DS18B20 w1_slave file.
    Returns (temperature_c, raw_data_string).
    """
    if not lines or len(lines) < 2:
        return None, ""

    # Line 1 contains CRC check
    crc_line = lines[0].strip()
    temp_line = lines[1].strip()
    raw_str = f"{crc_line} | {temp_line}"

    if not crc_line.endswith("YES"):
        return None, raw_str

    equals_pos = temp_line.find("t=")
    if equals_pos == -1:
        return None, raw_str

    try:
        raw_temp_val = temp_line[equals_pos + 2 :].strip()
        temp_c = float(raw_temp_val) / 1000.0
        return round(temp_c, 2), raw_str
    except (ValueError, IndexError):
        return None, raw_str


class TempCalibrationLogger:
    def __init__(
        self,
        sensor_type: str = "main",
        device_id: str | None = None,
        db_path: Path = DEFAULT_DB_FILE,
        interval: int = DEFAULT_INTERVAL_SECONDS,
        reference_temp: float | None = None,
        notes: str | None = None,
        simulate: bool = False,
        reset: bool = False,
    ):
        # Normalize sensor type
        sensor_norm = sensor_type.lower().strip()
        if sensor_norm in ("1", "main", "m", "main_tank", "primary"):
            self.sensor_key = "main"
        elif sensor_norm in ("2", "mixing", "mix", "mixing_tank", "secondary"):
            self.sensor_key = "mixing"
        else:
            self.sensor_key = "main"

        self.sensor_info = SENSOR_CONFIGS[self.sensor_key]
        self.sensor_label = self.sensor_info["label"]
        self.device_id = device_id or self.sensor_info["default_device_id"]
        self.db_path = Path(db_path)
        self.interval = interval
        self.reference_temp = reference_temp
        self.notes = notes
        self.simulate = simulate
        self.reset = reset

        self.device_file = W1_BASE_DIR / self.device_id / "w1_slave"
        self.conn = None
        self.is_hardware_available = False

        # Session tracking
        self.session_readings = []
        self.running = True

        self._init_db()
        self._init_hardware()

    def _init_db(self):
        """Initializes the dedicated SQLite database and table for temperature calibration."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            if self.reset:
                self.conn.execute("DROP TABLE IF EXISTS temp_calibration_readings")
                print(f"[INFO] Cleared previous readings from {self.db_path.name} (--reset).")

            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS temp_calibration_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    sensor_name TEXT NOT NULL,
                    sensor_id TEXT NOT NULL,
                    temperature_c REAL NOT NULL,
                    temperature_f REAL NOT NULL,
                    raw_data TEXT,
                    notes TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_temp_cal_timestamp
                ON temp_calibration_readings (timestamp)
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_temp_cal_sensor
                ON temp_calibration_readings (sensor_name)
                """
            )

    def _init_hardware(self):
        """Checks for 1-Wire DS18B20 hardware availability or sets simulation mode."""
        if self.simulate:
            print(f"[INFO] Running in SIMULATION mode for {self.sensor_label} as requested by flag.")
            self.is_hardware_available = False
            return

        # Attempt to load 1-Wire kernel drivers on Linux/RPi if running on hardware
        if os.name == "posix" and os.path.exists("/sys/bus"):
            try:
                os.system("modprobe w1-gpio 2>/dev/null")
                os.system("modprobe w1-therm 2>/dev/null")
            except Exception:
                pass

        if self.device_file.exists():
            self.is_hardware_available = True
            print(f"[HARDWARE] DS18B20 sensor found for {self.sensor_label}: {self.device_file}")
        else:
            self.is_hardware_available = False
            print(
                f"[WARNING] DS18B20 sensor file not found ({self.device_file}). "
                f"Falling back to simulated temperature readings."
            )

    def read_temperature(self) -> tuple[float, float, str]:
        """
        Reads temperature from DS18B20 hardware file.
        Returns (temp_c, temp_f, raw_data).
        Falls back to realistic simulation if hardware is unavailable.
        """
        if self.is_hardware_available and self.device_file.exists():
            for retry in range(5):
                try:
                    with open(self.device_file, "r") as f:
                        lines = f.readlines()
                    temp_c, raw_data = parse_w1_slave_data(lines)
                    if temp_c is not None:
                        temp_f = round(temp_c * 9.0 / 5.0 + 32.0, 2)
                        return temp_c, temp_f, raw_data
                    time.sleep(0.2)
                except Exception as e:
                    print(f"[WARNING] Retry {retry+1}/5 reading {self.sensor_label}: {e}")
                    time.sleep(0.2)
            print(f"[ERROR] Failed to read hardware after retries. Using simulated fallback reading.")

        # Realistic simulation fallback
        # Simulate realistic tank temperature around 25.5°C +/- small variance
        base_sim = 25.5 if self.sensor_key == "main" else 26.0
        temp_c = round(base_sim + random.uniform(-0.35, 0.35), 2)
        temp_f = round(temp_c * 9.0 / 5.0 + 32.0, 2)
        raw_sim = f"SIMULATED t={int(temp_c * 1000)}"
        return temp_c, temp_f, raw_sim

    def record_reading(
        self,
        temp_c: float,
        temp_f: float,
        raw_data: str = "",
        timestamp: str | None = None,
    ) -> int:
        """Stores temperature reading and timestamp into the SQLite database."""
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO temp_calibration_readings
            (timestamp, sensor_name, sensor_id, temperature_c, temperature_f, raw_data, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                self.sensor_key,
                self.device_id,
                temp_c,
                temp_f,
                raw_data,
                self.notes,
            ),
        )
        self.conn.commit()
        reading_id = cursor.lastrowid
        self.session_readings.append(temp_c)
        return reading_id

    def display_reading(
        self,
        reading_id: int,
        timestamp: str,
        temp_c: float,
        temp_f: float,
        raw_data: str,
    ):
        """Displays formatted reading in the terminal."""
        sample_num = len(self.session_readings)
        mode_tag = "[HARDWARE]" if self.is_hardware_available else "[SIMULATED]"
        ref_str = ""
        if self.reference_temp is not None:
            diff = temp_c - self.reference_temp
            sign = "+" if diff >= 0 else ""
            ref_str = f" | Offset from Ref ({self.reference_temp:.2f}°C): {sign}{diff:.2f}°C"
        note_str = f" | Notes: {self.notes}" if self.notes else ""

        print(
            f"{mode_tag} #{sample_num:03d} | {timestamp} | "
            f"{self.sensor_label} ({self.device_id}) | "
            f"Temp: {temp_c:5.2f} °C ({temp_f:5.2f} °F) | "
            f"DB ID: {reading_id}{ref_str}{note_str}"
        )

    def print_summary(self):
        """Prints a statistical summary of the session when finished."""
        print("\n" + "=" * 70)
        print("          DS18B20 CALIBRATION SESSION SUMMARY")
        print("=" * 70)
        count = len(self.session_readings)
        print(f"Sensor Calibrated       : {self.sensor_label} (Key: '{self.sensor_key}')")
        print(f"Sensor 1-Wire ID        : {self.device_id}")
        print(f"Total Readings Recorded : {count}")
        print(f"Sampling Interval       : {self.interval} seconds")
        print(f"Calibration DB File     : {self.db_path.resolve()}")

        if count > 0:
            avg_c = sum(self.session_readings) / count
            min_c = min(self.session_readings)
            max_c = max(self.session_readings)
            spread_c = max_c - min_c

            avg_f = avg_c * 9.0 / 5.0 + 32.0
            min_f = min_c * 9.0 / 5.0 + 32.0
            max_f = max_c * 9.0 / 5.0 + 32.0

            # Calculate standard deviation for stability
            variance = sum((x - avg_c) ** 2 for x in self.session_readings) / count
            std_dev = math.sqrt(variance)

            print(f"Average Temperature     : {avg_c:.2f} °C ({avg_f:.2f} °F)")
            print(f"Minimum Temperature     : {min_c:.2f} °C ({min_f:.2f} °F)")
            print(f"Maximum Temperature     : {max_c:.2f} °C ({max_f:.2f} °F)")
            print(f"Temperature Spread      : {spread_c:.2f} °C ({spread_c * 1.8:.2f} °F)")
            print(f"Standard Deviation      : {std_dev:.4f} °C (Stability Index)")

            if self.reference_temp is not None:
                offset = avg_c - self.reference_temp
                sign = "+" if offset >= 0 else ""
                print("-" * 70)
                print(f"Known Reference Temp    : {self.reference_temp:.2f} °C")
                print(f"Calibration Offset      : {sign}{offset:.2f} °C (Measured - Ref)")
                print(f"Calibration Adjustment  : Subtract {sign}{offset:.2f} °C in software to align with reference")
        print("=" * 70)

    def close(self):
        """Closes the SQLite database connection safely."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def run(self):
        """Main loop that collects and stores readings every 20 seconds."""
        mode = "REAL HARDWARE (1-Wire)" if self.is_hardware_available else "SIMULATION"
        print("=" * 70)
        print("          DS18B20 TEMPERATURE SENSOR CALIBRATION LOGGER")
        print("=" * 70)
        print(f"Target Sensor : {self.sensor_label} (Key: '{self.sensor_key}')")
        print(f"1-Wire ID     : {self.device_id}")
        print(f"Database File : {self.db_path}")
        print(f"Interval      : {self.interval} seconds")
        print(f"Hardware Mode : {mode}")
        if self.reference_temp is not None:
            print(f"Reference Temp: {self.reference_temp:.2f} °C")
        if self.notes:
            print(f"Session Note  : {self.notes}")
        print("-" * 70)
        print("Press Ctrl+C at any time to finish and view calibration summary.")
        print("-" * 70)

        # Setup graceful signal handler
        def handle_signal(sig, frame):
            self.running = False

        try:
            signal.signal(signal.SIGTERM, handle_signal)
        except (ValueError, AttributeError):
            pass

        try:
            while self.running:
                # 1. Take reading
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                temp_c, temp_f, raw_data = self.read_temperature()

                # 2. Store in SQLite
                record_id = self.record_reading(temp_c, temp_f, raw_data, timestamp)

                # 3. Display in terminal
                self.display_reading(record_id, timestamp, temp_c, temp_f, raw_data)

                # 4. Wait for next reading with countdown / responsive interrupt handling
                self._wait_interval()

        except KeyboardInterrupt:
            print("\n[INFO] Calibration stopped by user.")
        finally:
            self.print_summary()
            self.close()

    def _wait_interval(self):
        """Waits for self.interval seconds, checking every 0.5s for clean interruption."""
        start_time = time.time()
        while time.time() - start_time < self.interval:
            if not self.running:
                break
            time.sleep(0.5)


def prompt_sensor_selection() -> str:
    """
    Displays an interactive selection menu allowing the user to choose
    between Main Tank and Mixing Tank sensor.
    """
    detected_devices = discover_w1_sensors()
    main_detected = SENSOR_CONFIGS["main"]["default_device_id"] in detected_devices
    mixing_detected = SENSOR_CONFIGS["mixing"]["default_device_id"] in detected_devices

    print("\n" + "=" * 65)
    print("      SELECT TEMPERATURE SENSOR FOR CALIBRATION")
    print("=" * 65)

    status_main = "[DETECTED ONLINE]" if main_detected else "[CONFIGURED]"
    status_mix = "[DETECTED ONLINE]" if mixing_detected else "[CONFIGURED]"

    print(f"  [1] Main Tank Sensor   ({SENSOR_CONFIGS['main']['default_device_id']}) {status_main}")
    print(f"  [2] Mixing Tank Sensor ({SENSOR_CONFIGS['mixing']['default_device_id']}) {status_mix}")

    # List any other 1-Wire sensors found on the bus
    other_devices = [
        d for d in detected_devices
        if d not in (SENSOR_CONFIGS["main"]["default_device_id"], SENSOR_CONFIGS["mixing"]["default_device_id"])
    ]
    if other_devices:
        print("  Additional 1-Wire devices found on bus:")
        for dev in other_devices:
            print(f"    - {dev}")

    print("-" * 65)

    if not sys.stdin.isatty():
        print("[INFO] Non-interactive shell detected; defaulting to [1] Main Tank.")
        return "main"

    while True:
        try:
            choice = input("Enter choice [1 for Main Tank, 2 for Mixing Tank] (default: 1): ").strip()
            if not choice or choice in ("1", "main", "m"):
                return "main"
            elif choice in ("2", "mixing", "mix"):
                return "mixing"
            else:
                print("Invalid selection. Please enter 1 or 2.")
        except (EOFError, KeyboardInterrupt):
            print("\nDefaulting to Main Tank.")
            return "main"


def view_history(db_path: Path, sensor_filter: str | None = None, limit: int = 50):
    """Prints recent readings stored in the calibration database."""
    if not db_path.exists():
        print(f"No calibration database found at: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if sensor_filter:
        norm = "main" if sensor_filter in ("1", "main") else "mixing"
        cursor.execute(
            """
            SELECT id, timestamp, sensor_name, sensor_id, temperature_c, temperature_f, raw_data, notes
            FROM temp_calibration_readings
            WHERE sensor_name = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (norm, limit),
        )
    else:
        cursor.execute(
            """
            SELECT id, timestamp, sensor_name, sensor_id, temperature_c, temperature_f, raw_data, notes
            FROM temp_calibration_readings
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        filter_msg = f" for sensor '{sensor_filter}'" if sensor_filter else ""
        print(f"Database exists but contains no calibration readings yet{filter_msg}.")
        return

    title_filter = f" [{sensor_filter.upper()} ONLY]" if sensor_filter else ""
    print("=" * 85)
    print(f"   RECENT TEMPERATURE CALIBRATION READINGS{title_filter} (Last {len(rows)} from {db_path.name})")
    print("=" * 85)
    print(f"{'ID':<6} | {'Timestamp':<19} | {'Sensor':<10} | {'Temp (°C)':<9} | {'Temp (°F)':<9} | Notes")
    print("-" * 85)
    for row in reversed(rows):
        notes = row["notes"] or ""
        print(
            f"{row['id']:<6} | {row['timestamp']:<19} | {row['sensor_name']:<10} | "
            f"{row['temperature_c']:<9.2f} | {row['temperature_f']:<9.2f} | {notes}"
        )
    print("=" * 85)


def export_csv(db_path: Path, output_file: Path, sensor_filter: str | None = None):
    """Exports calibration readings from the SQLite database to a CSV file."""
    if not db_path.exists():
        print(f"Error: Database {db_path} does not exist.")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if sensor_filter:
        norm = "main" if sensor_filter in ("1", "main") else "mixing"
        cursor.execute(
            """
            SELECT id, timestamp, sensor_name, sensor_id, temperature_c, temperature_f, raw_data, notes
            FROM temp_calibration_readings
            WHERE sensor_name = ?
            ORDER BY id ASC
            """,
            (norm,),
        )
    else:
        cursor.execute(
            """
            SELECT id, timestamp, sensor_name, sensor_id, temperature_c, temperature_f, raw_data, notes
            FROM temp_calibration_readings
            ORDER BY id ASC
            """
        )

    rows = cursor.fetchall()
    conn.close()

    output_path = Path(output_file)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["ID", "Timestamp", "Sensor Name", "1-Wire Sensor ID", "Temp (°C)", "Temp (°F)", "Raw Reading", "Notes"]
        )
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["timestamp"],
                    row["sensor_name"],
                    row["sensor_id"],
                    row["temperature_c"],
                    row["temperature_f"],
                    row["raw_data"] or "",
                    row["notes"] or "",
                ]
            )

    print(f"Exported {len(rows)} temperature calibration readings to {output_path.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="DS18B20 Temperature Sensor Calibration Tool: Displays and logs temperature readings every 20 seconds to a dedicated SQLite database."
    )
    parser.add_argument(
        "--sensor",
        "-s",
        type=str,
        default=None,
        choices=["main", "mixing", "1", "2"],
        help="Select which sensor to calibrate: 'main' (1) or 'mixing' (2). If omitted, an interactive menu is displayed.",
    )
    parser.add_argument(
        "--device-id",
        "-d",
        type=str,
        default=None,
        help="Custom 1-Wire device ID (e.g. '28-000000b197bd') to override defaults.",
    )
    parser.add_argument(
        "--interval",
        "-i",
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
        "--reference",
        "-r",
        type=float,
        default=None,
        help="Known reference temperature in °C (e.g., ice bath 0.0, boiling water 100.0, or certified thermometer)",
    )
    parser.add_argument(
        "--notes",
        "-n",
        type=str,
        default=None,
        help="Optional session note or label (e.g., 'Ice bath test', 'Mixing tank at 26C')",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Force simulation mode without attempting to read 1-Wire hardware",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear previous calibration readings in the database before starting",
    )
    parser.add_argument(
        "--view",
        action="store_true",
        help="View recent calibration records stored in the database and exit",
    )
    parser.add_argument(
        "--export-csv",
        type=Path,
        default=None,
        help="Export calibration records to a CSV file and exit",
    )

    args = parser.parse_args()

    # Handle View mode
    if args.view:
        view_history(args.db, sensor_filter=args.sensor)
        return

    # Handle CSV export mode
    if args.export_csv:
        export_csv(args.db, args.export_csv, sensor_filter=args.sensor)
        return

    # Sensor selection: CLI argument or interactive prompt
    sensor_type = args.sensor
    if sensor_type is None:
        sensor_type = prompt_sensor_selection()

    logger = TempCalibrationLogger(
        sensor_type=sensor_type,
        device_id=args.device_id,
        db_path=args.db,
        interval=args.interval,
        reference_temp=args.reference,
        notes=args.notes,
        simulate=args.simulate,
        reset=args.reset,
    )
    logger.run()


if __name__ == "__main__":
    main()
