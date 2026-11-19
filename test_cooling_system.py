#!/usr/bin/env python3
"""
Dedicated Standalone Cooling System Test & Dual-Tank Temperature Logger

Controls:
  - 8 Cooling Peltiers connected to GPIO: 14, 15, 7, 1, 12, 16, 20, 21
  - Relay Logic: Active-LOW (GPIO.LOW = ON / Energized, GPIO.HIGH = OFF / De-energized)

Fans & Valves:
  - Not controlled by this script.
  - Fans are configured to be constantly ON.
  - Water valve is configured to be constantly OPEN.

Sensors & Logging:
  - Checks detection status of Main Tank and Mixing Tank DS18B20 temperature sensors.
  - Prompts Y/N to confirm before starting the cooling system.
  - Logs temperature readings from both tanks every 3 seconds into a separate SQLite database.
  - Displays live readings and Peltier operational status in the terminal in real time.
  - Safely turns off all 8 Peltier elements and cleans up GPIO on exit (Ctrl+C).

Usage:
  python test_cooling_system.py                    # Interactive run (prompts Y/N)
  python test_cooling_system.py --yes              # Auto-confirm prompt
  python test_cooling_system.py --db my_test.db    # Custom database file
  python test_cooling_system.py --duration 30      # Run for 30 seconds then exit
  python test_cooling_system.py --simulate         # Force simulation mode (e.g. for testing on PC)
  python test_cooling_system.py --export-csv       # Export saved database to cooling_test.csv and exit
  python test_cooling_system.py --export-csv out.csv # Export saved database to out.csv and exit
"""

import argparse
import csv
from datetime import datetime
import os
from pathlib import Path
import random
import signal
import sqlite3
import sys
import time

# Base directory for resolving project modules and relative database paths
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Hardware Pin Configuration (8 Cooling Peltiers)
PELTIER_PINS = [14, 15, 7, 1, 12, 16, 20, 21]

# Default test configuration
DEFAULT_DB_FILE = BASE_DIR / "cooling_test.db"
DEFAULT_INTERVAL_SECONDS = 3.0

# Active-Low Relay definitions
RELAY_ON = 0    # GPIO.LOW
RELAY_OFF = 1   # GPIO.HIGH

# Check Raspberry Pi GPIO hardware presence
isRpiPresent = True
try:
    import RPi.GPIO as GPIO
    RELAY_ON = GPIO.LOW
    RELAY_OFF = GPIO.HIGH
except ImportError:
    isRpiPresent = False
    GPIO = None

# Internal state tracking
peltier_states = {pin: False for pin in PELTIER_PINS}
cooling_active = False


def get_temp_sensor_service():
    """
    Safely imports the DS18B20 temp_sensor service module without requiring Flask.
    """
    try:
        from app.services import temp_sensor
        return temp_sensor
    except Exception:
        import importlib.util
        temp_sensor_path = BASE_DIR / "app" / "services" / "temp_sensor.py"
        if temp_sensor_path.exists():
            try:
                spec = importlib.util.spec_from_file_location("standalone_temp_sensor", str(temp_sensor_path))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    return mod
            except Exception:
                pass
    return None


def setup_gpio():
    """
    Initializes strictly the 8 Peltier pins as outputs.
    All pins default to OFF (GPIO.HIGH / inactive) immediately.
    """
    global peltier_states, cooling_active
    cooling_active = False
    for pin in PELTIER_PINS:
        peltier_states[pin] = False

    if isRpiPresent and GPIO is not None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in PELTIER_PINS:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)


def set_peltiers(active: bool):
    """
    Turns all 8 cooling peltiers ON (active=True, GPIO.LOW) or OFF (active=False, GPIO.HIGH).
    """
    global peltier_states, cooling_active
    cooling_active = active
    state_str = "ON" if active else "OFF"
    output_val = RELAY_ON if active else RELAY_OFF

    for pin in PELTIER_PINS:
        peltier_states[pin] = active
        if isRpiPresent and GPIO is not None:
            GPIO.output(pin, output_val)

    mode = "RPI" if (isRpiPresent and GPIO is not None) else "DUMMY"
    pins_formatted = ", ".join(str(p) for p in PELTIER_PINS)
    print(f"[{mode}] 8 Cooling Peltiers (GPIO {pins_formatted}) -> {state_str}")


def cleanup_hardware():
    """
    Safely de-energizes all 8 Peltiers and cleans up strictly their GPIO pins.
    """
    global cooling_active
    try:
        set_peltiers(False)
    except Exception:
        pass
    cooling_active = False

    if isRpiPresent and GPIO is not None:
        try:
            GPIO.cleanup(PELTIER_PINS)
        except Exception:
            pass


def init_database(db_path: Path, reset: bool = False) -> sqlite3.Connection:
    """
    Initializes the dedicated SQLite database and creates the readings table if not present.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    with conn:
        if reset:
            conn.execute("DROP TABLE IF EXISTS cooling_test_readings")
            print(f"[DB] Reset existing readings in {db_path.name}")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cooling_test_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                main_tank_temp REAL,
                mixing_tank_temp REAL,
                peltiers_active INTEGER NOT NULL,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cooling_test_ts
            ON cooling_test_readings (timestamp)
            """
        )
    return conn


def log_reading(
    conn: sqlite3.Connection,
    timestamp: str,
    main_temp: float | None,
    mixing_temp: float | None,
    peltiers_active: bool,
    notes: str = ""
) -> int:
    """
    Inserts a single sensor reading record into the database and commits immediately.
    Returns the newly inserted row ID.
    """
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO cooling_test_readings
                (timestamp, main_tank_temp, mixing_tank_temp, peltiers_active, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                main_temp,
                mixing_temp,
                1 if peltiers_active else 0,
                notes
            )
        )
        return cursor.lastrowid


def export_csv(db_path: Path | str, output_file: Path | str) -> Path | None:
    """
    Exports cooling test readings from the SQLite database to a CSV file.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"Error: Database {db_path} does not exist.")
        return None

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT id, timestamp, main_tank_temp, mixing_tank_temp, peltiers_active, notes
            FROM cooling_test_readings
            ORDER BY id ASC
            """
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError as e:
        print(f"Error reading cooling test readings from {db_path}: {e}")
        return None
    finally:
        conn.close()

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["ID", "Timestamp", "Main Tank Temp (°C)", "Mixing Tank Temp (°C)", "Peltiers Active", "Notes"]
        )
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["timestamp"],
                    row["main_tank_temp"] if row["main_tank_temp"] is not None else "",
                    row["mixing_tank_temp"] if row["mixing_tank_temp"] is not None else "",
                    row["peltiers_active"],
                    row["notes"] or "",
                ]
            )

    print(f"Exported {len(rows)} cooling test readings to {output_path.resolve()}")
    return output_path


def check_temperature_sensors(
    main_id: str | None = None,
    mixing_id: str | None = None,
    simulate: bool = False
) -> dict:
    """
    Scans for 1-Wire DS18B20 devices, checks availability for both tanks,
    and returns a status dictionary.
    """
    status = {
        "main": {
            "name": "Main Tank Temperature",
            "device_id": main_id,
            "device_file": None,
            "detected": False,
            "sample_temp": None,
            "message": "",
        },
        "mixing": {
            "name": "Mixing Tank Temperature",
            "device_id": mixing_id,
            "device_file": None,
            "detected": False,
            "sample_temp": None,
            "message": "",
        },
        "discovered_devices": [],
        "simulated": simulate,
    }

    if simulate:
        status["main"]["detected"] = True
        status["main"]["device_id"] = main_id or "SIMULATED_MAIN_28-000000b197bd"
        status["main"]["sample_temp"] = 26.50
        status["main"]["message"] = "Simulated Sensor (Simulation Flag Active)"

        status["mixing"]["detected"] = True
        status["mixing"]["device_id"] = mixing_id or "SIMULATED_MIXING_28-000000b260f4"
        status["mixing"]["sample_temp"] = 26.80
        status["mixing"]["message"] = "Simulated Sensor (Simulation Flag Active)"
        return status

    ts = get_temp_sensor_service()
    if not ts:
        status["main"]["message"] = "temp_sensor service module not available"
        status["mixing"]["message"] = "temp_sensor service module not available"
        return status

    try:
        ts.ensure_w1_kernel_modules()
        discovered = ts.discover_w1_sensors()
        status["discovered_devices"] = discovered

        # Resolve Main Tank file & device ID
        if main_id:
            main_file = ts.W1_BASE_DIR / main_id / "w1_slave"
            resolved_main_id = main_id
        elif ts.MAIN_TANK_DEVICE_FILE.exists():
            main_file = ts.MAIN_TANK_DEVICE_FILE
            resolved_main_id = ts.MAIN_TANK_DEVICE_ID
        elif discovered:
            # If default not present, select first discovered device not explicitly assigned to mixing
            candidates = [d for d in discovered if d != (mixing_id or ts.MIXING_TANK_DEVICE_ID)]
            resolved_main_id = candidates[0] if candidates else discovered[0]
            main_file = ts.W1_BASE_DIR / resolved_main_id / "w1_slave"
        else:
            main_file = ts.MAIN_TANK_DEVICE_FILE
            resolved_main_id = ts.MAIN_TANK_DEVICE_ID

        status["main"]["device_id"] = resolved_main_id
        status["main"]["device_file"] = main_file

        if main_file and ts.is_w1_sensor_available(main_file):
            sample = ts.read_ds18b20(main_file, retries=2, retry_delay=0.1)
            status["main"]["detected"] = True
            status["main"]["sample_temp"] = sample
            status["main"]["message"] = f"Online at {main_file}"
        else:
            status["main"]["detected"] = False
            status["main"]["message"] = f"Device file not found ({main_file})"

        # Resolve Mixing Tank file & device ID
        if mixing_id:
            mixing_file = ts.W1_BASE_DIR / mixing_id / "w1_slave"
            resolved_mixing_id = mixing_id
        elif ts.MIXING_TANK_DEVICE_FILE.exists():
            mixing_file = ts.MIXING_TANK_DEVICE_FILE
            resolved_mixing_id = ts.MIXING_TANK_DEVICE_ID
        elif discovered:
            # Pick a discovered device that is not the one used by main tank
            candidates = [d for d in discovered if d != status["main"]["device_id"]]
            if candidates:
                resolved_mixing_id = candidates[0]
                mixing_file = ts.W1_BASE_DIR / resolved_mixing_id / "w1_slave"
            else:
                resolved_mixing_id = discovered[0]
                mixing_file = ts.W1_BASE_DIR / resolved_mixing_id / "w1_slave"
        else:
            mixing_file = ts.MIXING_TANK_DEVICE_FILE
            resolved_mixing_id = ts.MIXING_TANK_DEVICE_ID

        status["mixing"]["device_id"] = resolved_mixing_id
        status["mixing"]["device_file"] = mixing_file

        if mixing_file and ts.is_w1_sensor_available(mixing_file):
            sample = ts.read_ds18b20(mixing_file, retries=2, retry_delay=0.1)
            status["mixing"]["detected"] = True
            status["mixing"]["sample_temp"] = sample
            status["mixing"]["message"] = f"Online at {mixing_file}"
        else:
            status["mixing"]["detected"] = False
            status["mixing"]["message"] = f"Device file not found ({mixing_file})"

    except Exception as e:
        status["main"]["message"] = f"Error during discovery: {e}"
        status["mixing"]["message"] = f"Error during discovery: {e}"

    return status


def print_sensor_status_banner(sensor_status: dict, db_path: Path, interval: float):
    """
    Displays the status of both temperature sensors and test configuration in the terminal.
    """
    mode_str = "Raspberry Pi GPIO (Hardware)" if (isRpiPresent and GPIO is not None) else "Simulation / Dummy (No RPi.GPIO)"
    pins_str = ", ".join(str(p) for p in PELTIER_PINS)

    print("\n" + "=" * 76)
    print("           COOLING SYSTEM & DUAL TANK TEMPERATURE TEST BENCH")
    print("=" * 76)
    print(f" Hardware Mode      : {mode_str}")
    print(f" Peltier Pins (8)   : GPIO {pins_str}  [Active-LOW Relay]")
    print(f" Fans & Water Valve : NOT controlled (Fans constantly ON, Valve constantly OPEN)")
    print(f" Logging Interval   : Every {interval:.1f} second(s)")
    print(f" Database Output    : {db_path.resolve()}")
    print("-" * 76)
    print(" TEMPERATURE SENSORS STATUS:")

    for key, label in [("main", "Main Tank Sensor"), ("mixing", "Mixing Tank Sensor")]:
        info = sensor_status[key]
        det = info["detected"]
        status_tag = " [ DETECTED ] " if det else " [ NOT DETECTED ] "
        color_flag = "\033[92m" if det else "\033[91m"
        reset_color = "\033[0m"

        dev_id = info["device_id"] or "N/A"
        sample = f"{info['sample_temp']:.2f} °C" if info["sample_temp"] is not None else "N/A"
        file_path = str(info["device_file"]) if info["device_file"] else "N/A"

        print(f"   • {label:<20}: {color_flag}{status_tag}{reset_color}")
        print(f"       Device ID    : {dev_id}")
        print(f"       Device File  : {file_path}")
        print(f"       Sample Temp  : {sample}")
        print(f"       Details      : {info['message']}")

    if sensor_status.get("discovered_devices"):
        print(f"   • 1-Wire Bus Devices Found : {', '.join(sensor_status['discovered_devices'])}")
    else:
        print("   • 1-Wire Bus Devices Found : None")
    print("=" * 76 + "\n")


def prompt_user_continue(default_yes: bool = False) -> bool:
    """
    Prompts the user with Y/N to continue or abort.
    Accepts y/yes/Y/YES or n/no/N/NO.
    """
    if default_yes:
        print("[PROMPT] Auto-continuing (--yes flag enabled).")
        return True

    if not sys.stdin.isatty():
        # Non-interactive environment, check for piped input
        try:
            line = sys.stdin.readline()
            if line:
                return line.strip().lower() in ("y", "yes")
        except Exception:
            pass
        print("[PROMPT] Non-interactive environment without explicit input. Defaulting to YES.")
        return True

    while True:
        try:
            choice = input("Do you want to continue and start the cooling system? (Y/N): ").strip().lower()
            if choice in ("y", "yes"):
                return True
            if choice in ("n", "no"):
                return False
            print("Invalid input. Please enter 'Y' to continue or 'N' to abort.")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted by user.")
            return False


def read_temperature_values(sensor_status: dict, sim_step: int = 0) -> tuple[float | None, float | None, str]:
    """
    Reads live temperatures from both sensors, falling back to simulated values if sensors
    are not detected or running in simulation mode.
    Returns: (main_temp, mixing_temp, notes)
    """
    ts = get_temp_sensor_service()
    notes = []

    # Main Tank
    main_temp = None
    if sensor_status["main"]["detected"] and sensor_status["main"]["device_file"] and ts:
        main_temp = ts.read_ds18b20(sensor_status["main"]["device_file"], retries=3, retry_delay=0.1)
    if main_temp is None:
        # Simulated cooling trend: starts around 27.0°C and slowly drops towards 23.5°C
        sim_drop = min(3.5, sim_step * 0.05)
        main_temp = round(27.0 - sim_drop + random.uniform(-0.08, 0.08), 2)
        notes.append("MainTank:Simulated")

    # Mixing Tank
    mixing_temp = None
    if sensor_status["mixing"]["detected"] and sensor_status["mixing"]["device_file"] and ts:
        mixing_temp = ts.read_ds18b20(sensor_status["mixing"]["device_file"], retries=3, retry_delay=0.1)
    if mixing_temp is None:
        # Simulated cooling trend in mixing tank: drops slightly faster
        sim_drop = min(4.2, sim_step * 0.065)
        mixing_temp = round(26.6 - sim_drop + random.uniform(-0.08, 0.08), 2)
        notes.append("MixingTank:Simulated")

    return main_temp, mixing_temp, "; ".join(notes)


def run_cooling_test(
    db_path: Path = DEFAULT_DB_FILE,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    duration: float | None = None,
    max_readings: int | None = None,
    main_id: str | None = None,
    mixing_id: str | None = None,
    simulate: bool = False,
    auto_yes: bool = False,
    reset_db: bool = False,
    export_csv_path: Path | str | None = None
):
    """
    Main test execution workflow.
    """
    db_path = Path(db_path)

    # 1. Inspect sensors and hardware
    sensor_status = check_temperature_sensors(main_id=main_id, mixing_id=mixing_id, simulate=simulate)
    print_sensor_status_banner(sensor_status, db_path, interval)

    # 2. Prompt user to continue
    if not prompt_user_continue(default_yes=auto_yes):
        print("\n[EXIT] User chose not to continue. Cooling system NOT started. Goodbye.")
        return

    # 3. Setup GPIO and database
    setup_gpio()
    conn = init_database(db_path, reset=reset_db)

    # Signal handlers for clean shutdown
    interrupted = False

    def handle_signal(sig, frame):
        nonlocal interrupted
        interrupted = True
        print("\n[SIGNAL] Termination requested. Shutting down cooling system...")

    old_sigint = signal.signal(signal.SIGINT, handle_signal)
    old_sigterm = signal.signal(signal.SIGTERM, handle_signal)

    # Statistics tracking
    main_readings = []
    mixing_readings = []
    total_logged = 0
    start_time = time.time()

    try:
        # 4. Turn ON the 8 cooling peltiers
        print("\n" + "=" * 76)
        print("  STARTING COOLING PROCESS & TEMPERATURE LOGGING")
        print("=" * 76)
        set_peltiers(True)
        print(f"[STATUS] 8 Peltiers are ON. Logging readings every {interval:.1f}s to '{db_path.name}'...")
        print("[TIP] Press Ctrl+C at any time to safely shut off peltiers and exit.\n")

        # Table header
        header = f"{'Timestamp':<20} | {'Step':<6} | {'Main Tank Temp':<16} | {'Mixing Tank Temp':<16} | {'Peltiers':<10} | {'DB Record'}"
        print("-" * len(header))
        print(header)
        print("-" * len(header))

        step = 0
        while not interrupted:
            step += 1
            now_dt = datetime.now()
            timestamp_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

            # Read temperatures
            main_t, mix_t, notes = read_temperature_values(sensor_status, sim_step=step)

            if main_t is not None:
                main_readings.append(main_t)
            if mix_t is not None:
                mixing_readings.append(mix_t)

            # Log to database
            record_id = log_reading(
                conn=conn,
                timestamp=timestamp_str,
                main_temp=main_t,
                mixing_temp=mix_t,
                peltiers_active=cooling_active,
                notes=notes
            )
            total_logged += 1

            # Format terminal output
            main_str = f"{main_t:.2f} °C" if main_t is not None else "ERROR"
            mix_str = f"{mix_t:.2f} °C" if mix_t is not None else "ERROR"
            peltier_str = "8/8 ON" if cooling_active else "OFF"
            rec_str = f"Logged (id={record_id})"

            print(f"{timestamp_str:<20} | #{step:<5} | {main_str:<16} | {mix_str:<16} | {peltier_str:<10} | {rec_str}")
            sys.stdout.flush()

            # Check limits
            elapsed = time.time() - start_time
            if duration is not None and elapsed >= duration:
                print(f"\n[INFO] Target duration of {duration:.1f}s reached.")
                break
            if max_readings is not None and step >= max_readings:
                print(f"\n[INFO] Target reading count of {max_readings} reached.")
                break

            # Sleep interval in small slices to remain responsive to Ctrl+C
            sleep_end = time.time() + interval
            while time.time() < sleep_end and not interrupted:
                time.sleep(min(0.2, sleep_end - time.time()))

    finally:
        # 5. Clean shutdown
        print("\n" + "=" * 76)
        print("  SHUTTING DOWN COOLING SYSTEM...")
        print("=" * 76)
        set_peltiers(False)
        cleanup_hardware()

        try:
            conn.close()
        except Exception:
            pass

        # Restore original signal handlers
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)

        # 6. Print Summary Report
        elapsed_sec = round(time.time() - start_time, 1)
        print("\n" + "=" * 76)
        print("                      TEST EXECUTION SUMMARY")
        print("=" * 76)
        print(f" Total Duration      : {elapsed_sec:.1f} second(s)")
        print(f" Total Logs Recorded : {total_logged}")
        print(f" Database Saved To   : {db_path.resolve()}")
        print(f" Final Peltier State : OFF (De-energized & GPIO Cleaned)")
        print("-" * 76)

        if main_readings:
            print(" MAIN TANK TEMPERATURE:")
            print(f"   Initial : {main_readings[0]:.2f} °C")
            print(f"   Final   : {main_readings[-1]:.2f} °C")
            print(f"   Min/Max : {min(main_readings):.2f} °C / {max(main_readings):.2f} °C")
            print(f"   Delta   : {main_readings[-1] - main_readings[0]:+.2f} °C")
        else:
            print(" MAIN TANK TEMPERATURE: No valid readings recorded")

        if mixing_readings:
            print(" MIXING TANK TEMPERATURE:")
            print(f"   Initial : {mixing_readings[0]:.2f} °C")
            print(f"   Final   : {mixing_readings[-1]:.2f} °C")
            print(f"   Min/Max : {min(mixing_readings):.2f} °C / {max(mixing_readings):.2f} °C")
            print(f"   Delta   : {mixing_readings[-1] - mixing_readings[0]:+.2f} °C")
        else:
            print(" MIXING TANK TEMPERATURE: No valid readings recorded")

        print("=" * 76 + "\n")

        if export_csv_path:
            export_csv(db_path, export_csv_path)


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Run cooling system (8 peltiers on GPIO 14, 15, 7, 1, 12, 16, 20, 21) and log tank temperatures."
    )
    parser.add_argument(
        "--interval", "-i",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Sampling and logging interval in seconds (default: {DEFAULT_INTERVAL_SECONDS}s)"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(DEFAULT_DB_FILE),
        help=f"Path to separate SQLite database file (default: {DEFAULT_DB_FILE.name})"
    )
    parser.add_argument(
        "--duration", "-d",
        type=float,
        default=None,
        help="Optional maximum test duration in seconds"
    )
    parser.add_argument(
        "--count", "-c",
        type=int,
        default=None,
        help="Optional maximum number of readings before stopping"
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Automatically confirm prompt to continue without waiting for user input"
    )
    parser.add_argument(
        "--simulate", "-s",
        action="store_true",
        help="Force simulation mode for sensors and hardware"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset/clear previous readings table in database file before logging"
    )
    parser.add_argument(
        "--main-id",
        type=str,
        default=None,
        help="Explicit 1-Wire device ID for Main Tank sensor (e.g. 28-000000b197bd)"
    )
    parser.add_argument(
        "--mixing-id",
        type=str,
        default=None,
        help="Explicit 1-Wire device ID for Mixing Tank sensor (e.g. 28-000000b260f4)"
    )
    parser.add_argument(
        "--export-csv", "--export", "-e",
        dest="export_csv",
        nargs="?",
        const="",
        default=None,
        type=str,
        help="Export saved database readings to a CSV file and exit (optional file path, default: <db_name>.csv)"
    )
    return parser.parse_args(args)


def main():
    args = parse_args()

    if args.export_csv is not None:
        db_path = Path(args.db)
        if args.export_csv:
            out_file = Path(args.export_csv)
        else:
            out_file = db_path.with_suffix(".csv")
        export_csv(db_path=db_path, output_file=out_file)
        return

    run_cooling_test(
        db_path=Path(args.db),
        interval=args.interval,
        duration=args.duration,
        max_readings=args.count,
        main_id=args.main_id,
        mixing_id=args.mixing_id,
        simulate=args.simulate,
        auto_yes=args.yes,
        reset_db=args.reset
    )


if __name__ == "__main__":
    main()
