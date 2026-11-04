#!/usr/bin/env python3
"""
pH Sensor Calibration & Voltage Logger

Reads voltage from the pH sensor (ADS1115 ADC Channel 0 by default) every 30 seconds,
displays the voltage readings in the terminal, and records each reading with a timestamp
in a dedicated SQLite database (separate from the main application's database).
"""

import argparse
import csv
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
DEFAULT_DB_FILE = BASE_DIR / "ph_calibration.db"
DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_ADC_CHANNEL = 0
DEFAULT_NEUTRAL_VOLTAGE = 2.50
DEFAULT_SLOPE = 0.18


class PHCalibrationLogger:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_FILE,
        interval: int = DEFAULT_INTERVAL_SECONDS,
        channel: int = DEFAULT_ADC_CHANNEL,
        notes: str = None,
        simulate: bool = False,
    ):
        self.db_path = Path(db_path)
        self.interval = interval
        self.channel = channel
        self.notes = notes
        self.simulate = simulate
        self.conn = None
        self.adc_service = None
        self.is_hardware_available = False

        # Session tracking
        self.session_readings = []
        self.running = True

        self._init_db()
        self._init_adc()

    def _init_db(self):
        """Initializes the dedicated SQLite database and table for calibration."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ph_calibration_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    voltage REAL NOT NULL,
                    estimated_ph REAL,
                    notes TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ph_cal_timestamp
                ON ph_calibration_readings (timestamp)
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
                print(f"[HARDWARE] ADS1115 ADC detected. Monitoring channel AIN{self.channel}.")
            else:
                self.is_hardware_available = False
                print("[WARNING] ADS1115 ADC not detected. Falling back to simulated voltage readings.")
        except Exception as e:
            self.is_hardware_available = False
            print(f"[WARNING] Could not initialize ADCService ({e}). Falling back to simulation.")

    def read_voltage(self) -> float:
        """
        Reads voltage from ADCService (with multi-sample filtering).
        Falls back to realistic simulation if hardware is unavailable.
        """
        if self.is_hardware_available and self.adc_service:
            try:
                voltage = self.adc_service.read_filtered_voltage(self.channel)
                return round(voltage, 4)
            except Exception as e:
                print(f"[ERROR] Failed to read ADC channel {self.channel}: {e}. Retrying with fallback.")

        # Simulated voltage centered around 2.50V (neutral pH)
        simulated_voltage = 2.50 + random.uniform(-0.35, 0.35)
        return round(simulated_voltage, 4)

    def compute_estimated_ph(self, voltage: float) -> float:
        """Computes approximate pH based on standard neutral voltage (2.50V) and slope (0.18V/pH)."""
        ph = 7.0 + (DEFAULT_NEUTRAL_VOLTAGE - voltage) / DEFAULT_SLOPE
        return round(max(0.0, min(14.0, ph)), 2)

    def record_reading(self, voltage: float, timestamp: str = None) -> int:
        """Stores voltage and timestamp into the SQLite database."""
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        estimated_ph = self.compute_estimated_ph(voltage)

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO ph_calibration_readings (timestamp, voltage, estimated_ph, notes)
            VALUES (?, ?, ?, ?)
            """,
            (timestamp, voltage, estimated_ph, self.notes),
        )
        self.conn.commit()
        reading_id = cursor.lastrowid
        self.session_readings.append(voltage)
        return reading_id

    def display_reading(self, reading_id: int, timestamp: str, voltage: float, estimated_ph: float):
        """Displays formatted reading in the terminal."""
        sample_num = len(self.session_readings)
        note_str = f" | Notes: {self.notes}" if self.notes else ""
        mode_tag = "[HARDWARE]" if self.is_hardware_available else "[SIMULATED]"
        print(
            f"{mode_tag} #{sample_num:03d} | {timestamp} | "
            f"Voltage: {voltage:.4f} V | Est. pH: {estimated_ph:5.2f} | "
            f"DB Record ID: {reading_id}{note_str}"
        )

    def print_summary(self):
        """Prints a statistical summary of the session when finished."""
        print("\n" + "=" * 65)
        print("          pH SENSOR CALIBRATION SESSION SUMMARY")
        print("=" * 65)
        count = len(self.session_readings)
        print(f"Total Readings Recorded : {count}")
        print(f"Calibration DB File    : {self.db_path.resolve()}")

        if count > 0:
            avg_v = sum(self.session_readings) / count
            min_v = min(self.session_readings)
            max_v = max(self.session_readings)
            spread = max_v - min_v
            print(f"Average Voltage         : {avg_v:.4f} V")
            print(f"Minimum Voltage         : {min_v:.4f} V")
            print(f"Maximum Voltage         : {max_v:.4f} V")
            print(f"Voltage Spread (Max-Min): {spread:.4f} V")
            print(f"Approx. Neutral pH (7.0): V_neutral = {avg_v:.4f} V (if submerged in pH 7 buffer)")
        print("=" * 65)

    def close(self):
        """Closes the SQLite database connection safely."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def run(self):
        """Main loop that collects and stores readings every 30 seconds."""
        mode = "REAL HARDWARE (ADS1115)" if self.is_hardware_available else "SIMULATION"
        print("=" * 65)
        print("              pH SENSOR CALIBRATION LOGGER")
        print("=" * 65)
        print(f"Database File : {self.db_path}")
        print(f"ADC Channel   : AIN{self.channel}")
        print(f"Interval      : {self.interval} seconds")
        print(f"Hardware Mode : {mode}")
        if self.notes:
            print(f"Session Note  : {self.notes}")
        print("-" * 65)
        print("Press Ctrl+C at any time to finish and view summary.")
        print("-" * 65)

        try:
            while self.running:
                # 1. Take reading
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                voltage = self.read_voltage()
                estimated_ph = self.compute_estimated_ph(voltage)

                # 2. Store in SQLite
                record_id = self.record_reading(voltage, timestamp)

                # 3. Display in terminal
                self.display_reading(record_id, timestamp, voltage, estimated_ph)

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
        SELECT id, timestamp, voltage, estimated_ph, notes
        FROM ph_calibration_readings
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

    print("=" * 70)
    print(f"   RECENT pH CALIBRATION READINGS (Last {len(rows)} from {db_path.name})")
    print("=" * 70)
    print(f"{'ID':<6} | {'Timestamp':<19} | {'Voltage (V)':<11} | {'Est. pH':<8} | Notes")
    print("-" * 70)
    for row in reversed(rows):
        notes = row["notes"] or ""
        print(f"{row['id']:<6} | {row['timestamp']:<19} | {row['voltage']:<11.4f} | {row['estimated_ph']:<8.2f} | {notes}")
    print("=" * 70)


def export_csv(db_path: Path, output_file: Path):
    """Exports all calibration readings to a CSV file."""
    if not db_path.exists():
        print(f"Error: Database {db_path} does not exist.")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, timestamp, voltage, estimated_ph, notes FROM ph_calibration_readings ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Timestamp", "Voltage (V)", "Estimated pH", "Notes"])
        for row in rows:
            writer.writerow([row["id"], row["timestamp"], row["voltage"], row["estimated_ph"], row["notes"] or ""])

    print(f"Exported {len(rows)} calibration readings to {output_file.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="pH Sensor Calibration Tool: Displays and logs voltage readings to a separate SQLite database every 30 seconds."
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
        help="Optional label or note for this calibration session (e.g., 'pH 7.0 buffer')",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Force simulation mode without connecting to real ADC hardware",
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

    logger = PHCalibrationLogger(
        db_path=args.db,
        interval=args.interval,
        channel=args.channel,
        notes=args.notes,
        simulate=args.simulate,
    )
    logger.run()


if __name__ == "__main__":
    main()
