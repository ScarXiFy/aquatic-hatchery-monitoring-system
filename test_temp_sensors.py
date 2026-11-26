#!/usr/bin/env python3
"""
DS18B20 Multi-Sensor Temperature Test & Logger

Reads temperatures from the 4 configured DS18B20 1-Wire sensors every 3 seconds
by default, displays live readings in the terminal, stores all readings in a
separate SQLite database (temp_sensors_test.db), and supports exporting stored
data to CSV.
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

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_FILE = BASE_DIR / "temp_sensors_test.db"
DEFAULT_INTERVAL_SECONDS = 3.0
W1_BASE_DIR = Path("/sys/bus/w1/devices")

SENSOR_IDS = [
    "28-0623c360d871",
    "28-0623c3c91a42",
    "28-0623c3671909",
    "28-0923c0dab484",
]


def parse_w1_slave_data(lines: list[str]) -> tuple[float | None, str]:
    """
    Parses lines from a DS18B20 w1_slave file.
    Returns (temperature_c, raw_data_string).
    """
    if not lines or len(lines) < 2:
        return None, ""

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


def init_db(db_path: Path, reset: bool = False) -> sqlite3.Connection:
    """Initializes the separate SQLite database for multi-sensor test readings."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    with conn:
        if reset:
            conn.execute("DROP TABLE IF EXISTS sensor_readings")
            print(f"[INFO] Cleared previous readings from {db_path.name} (--reset).")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_num INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                sensor_id TEXT NOT NULL,
                temperature_c REAL,
                temperature_f REAL,
                status TEXT NOT NULL,
                raw_data TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_readings_timestamp
            ON sensor_readings (timestamp)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sensor_readings_sensor_id
            ON sensor_readings (sensor_id)
            """
        )
    return conn


class MultiTempSensorTester:
    def __init__(
        self,
        sensor_ids: list[str] = SENSOR_IDS,
        db_path: Path = DEFAULT_DB_FILE,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        simulate: bool = False,
        reset: bool = False,
        max_samples: int | None = None,
    ):
        self.sensor_ids = list(sensor_ids)
        self.db_path = Path(db_path)
        self.interval = interval
        self.simulate = simulate
        self.reset = reset
        self.max_samples = max_samples

        self.running = True
        self.session_readings: dict[str, list[float]] = {sid: [] for sid in self.sensor_ids}
        self.conn = init_db(self.db_path, reset=self.reset)

        self._init_hardware()

    def _init_hardware(self):
        """Loads 1-Wire kernel modules on Linux/RPi and checks sensor availability."""
        if self.simulate:
            print("[INFO] Running in SIMULATION mode (--simulate).")
            return

        if os.name == "posix" and os.path.exists("/sys/bus"):
            try:
                os.system("modprobe w1-gpio 2>/dev/null")
                os.system("modprobe w1-therm 2>/dev/null")
            except Exception:
                pass

        for sid in self.sensor_ids:
            device_file = W1_BASE_DIR / sid / "w1_slave"
            if device_file.exists():
                print(f"[HARDWARE] Found {sid}: {device_file}")
            else:
                print(f"[WARNING]  Sensor not found: {device_file}")

    def read_sensor(self, sensor_id: str) -> tuple[float | None, float | None, str, str]:
        """
        Reads a single DS18B20 sensor.
        Returns (temp_c, temp_f, status, raw_data).
        """
        if self.simulate:
            base = 25.0 + (self.sensor_ids.index(sensor_id) * 0.25)
            temp_c = round(base + random.uniform(-0.3, 0.3), 2)
            temp_f = round(temp_c * 9.0 / 5.0 + 32.0, 2)
            raw_data = f"SIMULATED t={int(temp_c * 1000)}"
            return temp_c, temp_f, "SIMULATED", raw_data

        device_file = W1_BASE_DIR / sensor_id / "w1_slave"
        if not device_file.exists():
            return None, None, "NOT_FOUND", f"Missing {device_file}"

        last_raw = ""
        for _ in range(3):
            try:
                with open(device_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                temp_c, raw_data = parse_w1_slave_data(lines)
                last_raw = raw_data
                if temp_c is not None:
                    temp_f = round(temp_c * 9.0 / 5.0 + 32.0, 2)
                    return temp_c, temp_f, "OK", raw_data
                time.sleep(0.2)
            except Exception as e:
                last_raw = str(e)
                time.sleep(0.2)

        return None, None, "ERROR", last_raw

    def record_sample(
        self,
        sample_num: int,
        timestamp: str,
        results: list[tuple[str, float | None, float | None, str, str]],
    ):
        """Stores all sensor readings for a single sample cycle into SQLite."""
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO sensor_readings
                (sample_num, timestamp, sensor_id, temperature_c, temperature_f, status, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (sample_num, timestamp, sid, temp_c, temp_f, status, raw_data)
                    for sid, temp_c, temp_f, status, raw_data in results
                ],
            )

        for sid, temp_c, _, _, _ in results:
            if temp_c is not None:
                self.session_readings[sid].append(temp_c)

    def display_sample(
        self,
        sample_num: int,
        timestamp: str,
        results: list[tuple[str, float | None, float | None, str, str]],
    ):
        """Prints the current sample's readings to the terminal."""
        print("-" * 58)
        print(f"Sample #{sample_num:03d} | {timestamp}")
        for sid, temp_c, temp_f, status, _ in results:
            if temp_c is not None and temp_f is not None:
                print(f"{sid}: {temp_c:6.2f} °C ({temp_f:6.2f} °F)  [{status}]")
            else:
                print(f"{sid}: {'N/A':>6}    ({'N/A':>6})     [{status}]")
        print("-" * 58)

    def print_summary(self):
        """Prints per-sensor statistics for the completed session."""
        print("\n" + "=" * 68)
        print("              DS18B20 MULTI-SENSOR TEST SUMMARY")
        print("=" * 68)
        print(f"Database File     : {self.db_path.resolve()}")
        print(f"Sampling Interval : {self.interval}s")
        print("-" * 68)
        print(f"{'Sensor ID':<18} {'Count':>5} {'Avg (°C)':>10} {'Min (°C)':>10} {'Max (°C)':>10} {'StdDev':>9}")
        print("-" * 68)
        for sid in self.sensor_ids:
            vals = self.session_readings[sid]
            if vals:
                avg_c = sum(vals) / len(vals)
                min_c = min(vals)
                max_c = max(vals)
                std_dev = math.sqrt(sum((x - avg_c) ** 2 for x in vals) / len(vals))
                print(
                    f"{sid:<18} {len(vals):>5} {avg_c:>10.2f} {min_c:>10.2f} {max_c:>10.2f} {std_dev:>9.4f}"
                )
            else:
                print(f"{sid:<18} {0:>5} {'N/A':>10} {'N/A':>10} {'N/A':>10} {'N/A':>9}")
        print("=" * 68)

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def run(self):
        """Main polling loop."""
        mode = "SIMULATION" if self.simulate else "1-WIRE HARDWARE"
        print("=" * 58)
        print("         DS18B20 MULTI-SENSOR TEMPERATURE TEST")
        print("=" * 58)
        print(f"Sensors  : {', '.join(self.sensor_ids)}")
        print(f"Database : {self.db_path}")
        print(f"Interval : {self.interval} seconds")
        print(f"Mode     : {mode}")
        print("Press Ctrl+C to stop and view summary.")

        def handle_signal(sig, frame):
            self.running = False

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        # Continue sample numbering from existing DB rows
        cursor = self.conn.execute("SELECT COALESCE(MAX(sample_num), 0) FROM sensor_readings")
        sample_num = cursor.fetchone()[0]
        completed_this_run = 0

        try:
            while self.running:
                cycle_start = time.monotonic()
                sample_num += 1
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                results = []
                for sid in self.sensor_ids:
                    temp_c, temp_f, status, raw_data = self.read_sensor(sid)
                    results.append((sid, temp_c, temp_f, status, raw_data))

                self.record_sample(sample_num, timestamp, results)
                self.display_sample(sample_num, timestamp, results)

                completed_this_run += 1
                if self.max_samples is not None and completed_this_run >= self.max_samples:
                    break

                # Sleep remaining time in small increments for responsive Ctrl+C
                elapsed = time.monotonic() - cycle_start
                remaining = max(0.0, self.interval - elapsed)
                wake_time = time.monotonic() + remaining
                while self.running and time.monotonic() < wake_time:
                    time.sleep(min(0.1, wake_time - time.monotonic()))
        finally:
            self.print_summary()
            self.close()


def view_history(db_path: Path, limit: int = 20):
    """Displays recent sensor readings from the SQLite database."""
    if not db_path.exists():
        print(f"[ERROR] Database file not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, sample_num, timestamp, sensor_id, temperature_c, temperature_f, status
        FROM sensor_readings
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"[INFO] No readings stored in {db_path}.")
        return

    print(f"\nShowing last {len(rows)} rows from {db_path}:")
    print("-" * 82)
    print(f"{'ID':>5} {'Sample':>6} {'Timestamp':<20} {'Sensor ID':<18} {'Temp (°C)':>10} {'Temp (°F)':>10} {'Status':<10}")
    print("-" * 82)
    for r in reversed(rows):
        tc = f"{r['temperature_c']:.2f}" if r["temperature_c"] is not None else "N/A"
        tf = f"{r['temperature_f']:.2f}" if r["temperature_f"] is not None else "N/A"
        print(
            f"{r['id']:>5} {r['sample_num']:>6} {r['timestamp']:<20} "
            f"{r['sensor_id']:<18} {tc:>10} {tf:>10} {r['status']:<10}"
        )
    print("-" * 82)


def export_csv(db_path: Path, output_file: Path, csv_format: str = "long"):
    """Exports stored readings from the SQLite database to a CSV file."""
    if not db_path.exists():
        print(f"[ERROR] Database file not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, sample_num, timestamp, sensor_id, temperature_c, temperature_f, status, raw_data
        FROM sensor_readings
        ORDER BY sample_num ASC, id ASC
        """
    ).fetchall()
    conn.close()

    output_path = Path(output_file)
    if csv_format == "wide":
        # Pivot by sample_num so each row has all sensors side by side
        samples: dict[int, dict] = {}
        seen_sensors = list(SENSOR_IDS)
        for r in rows:
            if r["sensor_id"] not in seen_sensors:
                seen_sensors.append(r["sensor_id"])
            entry = samples.setdefault(
                r["sample_num"],
                {"sample_num": r["sample_num"], "timestamp": r["timestamp"], "values": {}},
            )
            entry["values"][r["sensor_id"]] = r["temperature_c"]

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Sample", "Timestamp", *[f"{sid} (°C)" for sid in seen_sensors]])
            for sample_num in sorted(samples):
                entry = samples[sample_num]
                writer.writerow(
                    [
                        entry["sample_num"],
                        entry["timestamp"],
                        *[
                            "" if entry["values"].get(sid) is None else entry["values"][sid]
                            for sid in seen_sensors
                        ],
                    ]
                )
        print(f"Exported {len(samples)} sample cycles (wide format) to {output_path.resolve()}")
    else:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "ID",
                    "Sample",
                    "Timestamp",
                    "Sensor ID",
                    "Temp (°C)",
                    "Temp (°F)",
                    "Status",
                    "Raw Data",
                ]
            )
            for r in rows:
                writer.writerow(
                    [
                        r["id"],
                        r["sample_num"],
                        r["timestamp"],
                        r["sensor_id"],
                        "" if r["temperature_c"] is None else r["temperature_c"],
                        "" if r["temperature_f"] is None else r["temperature_f"],
                        r["status"],
                        r["raw_data"] or "",
                    ]
                )
        print(f"Exported {len(rows)} sensor readings (long format) to {output_path.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Read 4 DS18B20 1-Wire temperature sensors every 3 seconds, "
            "display readings in the terminal, log them to a separate SQLite DB, "
            "and export to CSV."
        )
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Sampling interval in seconds (default: {DEFAULT_INTERVAL_SECONDS:g}s)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_FILE,
        help=f"SQLite database file path (default: {DEFAULT_DB_FILE.name})",
    )
    parser.add_argument(
        "--samples",
        "-n",
        type=int,
        default=None,
        help="Optional number of sample cycles to record before stopping automatically",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run with simulated temperature readings instead of reading 1-Wire hardware",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear previous readings in the database before starting",
    )
    parser.add_argument(
        "--view",
        action="store_true",
        help="Display recent readings stored in the database and exit",
    )
    parser.add_argument(
        "--export-csv",
        type=Path,
        default=None,
        help="Export stored readings from the database to a CSV file and exit",
    )
    parser.add_argument(
        "--csv-format",
        choices=["long", "wide"],
        default="long",
        help="CSV layout for --export-csv: 'long' (one row per sensor reading) or 'wide' (one row per sample cycle)",
    )

    args = parser.parse_args()

    if args.view:
        view_history(args.db)
        return

    if args.export_csv:
        export_csv(args.db, args.export_csv, csv_format=args.csv_format)
        return

    tester = MultiTempSensorTester(
        sensor_ids=SENSOR_IDS,
        db_path=args.db,
        interval=args.interval,
        simulate=args.simulate,
        reset=args.reset,
        max_samples=args.samples,
    )
    tester.run()


if __name__ == "__main__":
    main()
