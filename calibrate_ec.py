#!/usr/bin/env python3
"""
EC (Electrical Conductivity / Salinity) Sensor Calibration & Voltage Logger

Reads analog voltage from the EC sensor (ADS1115 ADC Channel 1 by default) every 20 seconds,
displays the raw voltage readings in the terminal, and records each reading with a timestamp
into a dedicated SQLite database (ec_calibration.db).
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
DEFAULT_DB_FILE = BASE_DIR / "ec_calibration.db"
DEFAULT_INTERVAL_SECONDS = 20
DEFAULT_ADC_CHANNEL = 1  # Channel 1 matches SALINITY_ADC_CHANNEL in app/config.py
DEFAULT_MAX_VOLTAGE = 3.3
DEFAULT_MAX_SALINITY = 35.0


class ECCalibrationLogger:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_FILE,
        interval: int = DEFAULT_INTERVAL_SECONDS,
        channel: int = DEFAULT_ADC_CHANNEL,
        notes: str = None,
        simulate: bool = False,
        reset: bool = False,
    ):
        self.db_path = Path(db_path)
        self.interval = interval
        self.channel = channel
        self.notes = notes
        self.simulate = simulate
        self.reset = reset
        self.conn = None
        self.adc_service = None
        self.is_hardware_available = False

        # Session tracking
        self.session_readings = []
        self.running = True

        self._init_db()
        self._init_adc()

    def _init_db(self):
        """Initializes the dedicated SQLite database and table for EC calibration."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            if self.reset:
                self.conn.execute("DROP TABLE IF EXISTS ec_calibration_readings")
                print(f"[INFO] Cleared previous readings from {self.db_path.name} (--reset).")

            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ec_calibration_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    voltage REAL NOT NULL,
                    raw_voltage REAL NOT NULL,
                    estimated_ec REAL,
                    notes TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ec_cal_timestamp
                ON ec_calibration_readings (timestamp)
                """
            )

    def _init_adc(self):
        """Initializes ADC hardware or switches to simulation mode."""
        if self.simulate:
            print("[INFO] Running in SIMULATION mode as requested by flag.")
            self.is_hardware_available = False
            return

        try:
            from app.services.adc_service import ADCService

            self.adc_service = ADCService()
            if self.adc_service.available:
                self.is_hardware_available = True
                print(f"[HARDWARE] ADS1115 ADC detected. Monitoring channel AIN{self.channel} (EC/Salinity Sensor).")
            else:
                self.is_hardware_available = False
                print("[WARNING] ADS1115 ADC not detected. Falling back to simulated voltage readings.")
        except Exception as e:
            self.is_hardware_available = False
            print(f"[WARNING] Could not initialize ADCService ({e}). Falling back to simulation.")

    def read_voltage(self) -> float:
        """
        Reads raw voltage from ADCService with multi-sample filtering.
        Falls back to realistic simulation if hardware is unavailable.
        """
        if self.is_hardware_available and self.adc_service:
            try:
                voltage = self.adc_service.read_filtered_voltage(self.channel)
                return round(voltage, 4)
            except Exception as e:
                print(f"[ERROR] Failed to read ADC channel {self.channel}: {e}. Retrying with fallback.")

        # Simulated voltage centered around 1.65V (approx midpoint of 0-3.3V range)
        simulated_voltage = 1.65 + random.uniform(-0.15, 0.15)
        return round(simulated_voltage, 4)

    def compute_estimated_ec(self, voltage: float) -> float:
        """
        Computes approximate EC/salinity (ppt) based on linear mapping (0-3.3V -> 0-35 ppt).
        Provided as a live reference while recording raw voltages for exact calibration.
        """
        if voltage <= 0:
            return 0.0
        ec = (voltage / DEFAULT_MAX_VOLTAGE) * DEFAULT_MAX_SALINITY
        return round(max(0.0, min(50.0, ec)), 2)

    def record_reading(self, voltage: float, timestamp: str = None) -> int:
        """Stores raw voltage and timestamp into the SQLite database."""
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        estimated_ec = self.compute_estimated_ec(voltage)

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO ec_calibration_readings (timestamp, voltage, raw_voltage, estimated_ec, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp, voltage, voltage, estimated_ec, self.notes),
        )
        self.conn.commit()
        reading_id = cursor.lastrowid
        self.session_readings.append(voltage)
        return reading_id

    def display_reading(self, reading_id: int, timestamp: str, voltage: float, estimated_ec: float):
        """Displays formatted reading in the terminal."""
        sample_num = len(self.session_readings)
        note_str = f" | Notes: {self.notes}" if self.notes else ""
        mode_tag = "[HARDWARE]" if self.is_hardware_available else "[SIMULATED]"
        print(
            f"{mode_tag} #{sample_num:03d} | {timestamp} | "
            f"Raw Voltage: {voltage:.4f} V | Est. EC/Salinity: {estimated_ec:5.2f} ppt | "
            f"DB Record ID: {reading_id}{note_str}"
        )

    def print_summary(self):
        """Prints a statistical summary of the session when finished."""
        print("\n" + "=" * 70)
        print("          EC SENSOR CALIBRATION SESSION SUMMARY")
        print("=" * 70)
        count = len(self.session_readings)
        print(f"Total Readings Recorded : {count}")
        print(f"Sampling Interval       : {self.interval} seconds")
        print(f"Calibration DB File     : {self.db_path.resolve()}")
        if self.notes:
            print(f"Calibration Solution/Note: {self.notes}")

        if count > 0:
            avg_v = sum(self.session_readings) / count
            min_v = min(self.session_readings)
            max_v = max(self.session_readings)
            spread = max_v - min_v

            variance = sum((x - avg_v) ** 2 for x in self.session_readings) / count
            std_dev = math.sqrt(variance)

            print(f"Average Raw Voltage     : {avg_v:.4f} V")
            print(f"Minimum Raw Voltage     : {min_v:.4f} V")
            print(f"Maximum Raw Voltage     : {max_v:.4f} V")
            print(f"Voltage Spread (Max-Min): {spread:.4f} V")
            print(f"Standard Deviation      : {std_dev:.4f} V (Stability Index)")
            print("-" * 70)
            print("Calibration Tip:")
            print(f"  • Use V_avg = {avg_v:.4f} V as your calibration reference point for this solution.")
            if self.notes:
                print(f"  • Reference Standard: {self.notes} -> {avg_v:.4f} V")
        print("=" * 70)

    def close(self):
        """Closes the SQLite database connection safely."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def run(self):
        """Main loop that collects and stores readings every 20 seconds."""
        mode = "REAL HARDWARE (ADS1115)" if self.is_hardware_available else "SIMULATION"
        print("=" * 70)
        print("              EC SENSOR CALIBRATION LOGGER")
        print("=" * 70)
        print(f"Database File : {self.db_path}")
        print(f"ADC Channel   : AIN{self.channel} (EC / Salinity)")
        print(f"Interval      : {self.interval} seconds")
        print(f"Hardware Mode : {mode}")
        if self.notes:
            print(f"Session Note  : {self.notes}")
        print("-" * 70)
        print("Press Ctrl+C at any time to finish and view summary.")
        print("-" * 70)

        # Setup graceful signal handlers
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
                voltage = self.read_voltage()
                estimated_ec = self.compute_estimated_ec(voltage)

                # 2. Store in SQLite
                record_id = self.record_reading(voltage, timestamp)

                # 3. Display in terminal
                self.display_reading(record_id, timestamp, voltage, estimated_ec)

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


def view_history(db_path: Path, limit: int = 50):
    """Prints recent readings stored in the calibration database."""
    if not db_path.exists():
        print(f"No calibration database found at: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, timestamp, voltage, raw_voltage, estimated_ec, notes
        FROM ec_calibration_readings
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("Database exists but contains no calibration readings yet.")
        return

    print("=" * 75)
    print(f"   RECENT EC CALIBRATION READINGS (Last {len(rows)} from {db_path.name})")
    print("=" * 75)
    print(f"{'ID':<6} | {'Timestamp':<19} | {'Voltage (V)':<11} | {'Est. EC (ppt)':<13} | Notes")
    print("-" * 75)
    for row in reversed(rows):
        notes = row["notes"] or ""
        print(f"{row['id']:<6} | {row['timestamp']:<19} | {row['voltage']:<11.4f} | {row['estimated_ec']:<13.2f} | {notes}")
    print("=" * 75)


def export_csv(db_path: Path, output_file: Path):
    """Exports all calibration readings to a CSV file."""
    if not db_path.exists():
        print(f"Error: Database {db_path} does not exist.")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, timestamp, voltage, raw_voltage, estimated_ec, notes
        FROM ec_calibration_readings
        ORDER BY id ASC
        """
    )
    rows = cursor.fetchall()
    conn.close()

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Timestamp", "Voltage (V)", "Raw Voltage (V)", "Estimated EC (ppt)", "Notes"])
        for row in rows:
            writer.writerow([
                row["id"],
                row["timestamp"],
                row["voltage"],
                row["raw_voltage"],
                row["estimated_ec"],
                row["notes"] or "",
            ])

    print(f"Exported {len(rows)} calibration readings to {output_file.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="EC Sensor Calibration Tool: Displays and logs raw voltage readings every 20 seconds to a dedicated SQLite database."
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
        default=DEFAULT_ADC_CHANNEL,
        help=f"ADS1115 ADC channel (0-3, default: {DEFAULT_ADC_CHANNEL})",
    )
    parser.add_argument(
        "--notes",
        type=str,
        default=None,
        help="Optional label or note for this calibration session (e.g., '1413 uS/cm standard', '35 ppt salinity')",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Force simulation mode without connecting to real ADC hardware",
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
        help="Export all calibration records to a CSV file and exit",
    )

    args = parser.parse_args()

    if args.view:
        view_history(args.db)
        return

    if args.export_csv:
        export_csv(args.db, args.export_csv)
        return

    logger = ECCalibrationLogger(
        db_path=args.db,
        interval=args.interval,
        channel=args.channel,
        notes=args.notes,
        simulate=args.simulate,
        reset=args.reset,
    )
    logger.run()


if __name__ == "__main__":
    main()
