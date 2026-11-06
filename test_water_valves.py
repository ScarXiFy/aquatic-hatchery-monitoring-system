#!/usr/bin/env python3
"""
Water Valves Test Script: Cooling, Heating, and Main Water Valves

Turns on the cooling valve (GPIO 25), heating valve (GPIO 23), and main water valve (GPIO 24)
for a user-specified duration (in seconds), then safely closes all valves.

Active-Low Relay Logic:
  - ON  (Open)   : GPIO.LOW
  - OFF (Closed) : GPIO.HIGH

Usage:
  python test_water_valves.py 5              # Keep valves on for 5 seconds
  python test_water_valves.py --duration 10  # Keep valves on for 10 seconds
  python test_water_valves.py                # Prompts interactively for duration
"""

import argparse
import signal
import sys
import time
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import app.api.piUtils as piUtils


DEFAULT_DURATION = 5.0


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Test cooling, heating, and main water valves by turning them on for a specified duration."
    )
    parser.add_argument(
        "duration_pos",
        nargs="?",
        type=float,
        default=None,
        help="Duration in seconds to keep valves ON (positional argument, e.g. 5.0)",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=None,
        help=f"Duration in seconds to keep valves ON (default: {DEFAULT_DURATION}s)",
    )
    parser.add_argument(
        "--cooling-only",
        action="store_true",
        help="Test only the cooling valve (GPIO 25)",
    )
    parser.add_argument(
        "--heating-only",
        action="store_true",
        help="Test only the heating valve (GPIO 23)",
    )
    parser.add_argument(
        "--main-only",
        action="store_true",
        help="Test only the main water valve (GPIO 24)",
    )
    return parser.parse_args(args)


def get_target_duration(args) -> float:
    # Priority: 1. Positional arg, 2. --duration flag, 3. Interactive prompt, 4. Default
    if args.duration_pos is not None:
        duration = args.duration_pos
    elif args.duration is not None:
        duration = args.duration
    else:
        # Prompt user if stdin is interactive
        if sys.stdin.isatty():
            try:
                user_input = input(f"Enter duration in seconds to keep valves ON [{DEFAULT_DURATION}]: ").strip()
                if not user_input:
                    duration = DEFAULT_DURATION
                else:
                    duration = float(user_input)
            except (ValueError, EOFError):
                print(f"\n[INFO] Invalid input or interrupted. Using default duration of {DEFAULT_DURATION}s.")
                duration = DEFAULT_DURATION
        else:
            duration = DEFAULT_DURATION

    if duration <= 0:
        print("[WARNING] Duration must be greater than 0. Using default duration of 1.0s.")
        duration = 1.0

    return duration


def _set_main_valve(is_open: bool):
    if hasattr(piUtils, "setRpiMainValve"):
        piUtils.setRpiMainValve(is_open)
    else:
        piUtils.mainValve(is_open)


def _set_cooling_valve(is_open: bool):
    if hasattr(piUtils, "setRpiCoolingValve"):
        piUtils.setRpiCoolingValve(is_open)
    else:
        piUtils.coolingValve(is_open)


def _set_heating_valve(is_open: bool):
    if hasattr(piUtils, "setRpiHeatingValve"):
        piUtils.setRpiHeatingValve(is_open)
    else:
        piUtils.heatingValve(is_open)


def run_valves_test(duration: float, test_cooling: bool = True, test_heating: bool = True, test_main: bool = True):
    """
    Turns on the selected valves for `duration` seconds, then turns them off.
    Guarantees all valves are turned off even if interrupted.
    """
    mode_str = "Raspberry Pi Hardware (GPIO)" if piUtils.isRpiPresent else "Simulation Mode (Dummy)"
    selected_valves = []
    if test_main:
        selected_valves.append(f"Main Water Valve (GPIO {piUtils.mainValvePin})")
    if test_cooling:
        selected_valves.append(f"Cooling Valve (GPIO {piUtils.coolingValvePin})")
    if test_heating:
        selected_valves.append(f"Heating Valve (GPIO {piUtils.heatingValvePin})")

    print("\n" + "=" * 65)
    print("           WATER VALVES HARDWARE TEST BENCH")
    print("=" * 65)
    print(f"Mode         : {mode_str}")
    print(f"Test Duration: {duration:.1f} second(s)")
    print("Valves to ON :")
    for v in selected_valves:
        print(f"  • {v}")
    print("=" * 65 + "\n")

    # Flag to stop loop cleanly on signal
    interrupted = False

    def handle_signal(sig, frame):
        nonlocal interrupted
        print("\n\n[WARNING] Interrupt received! Immediately shutting off valves...")
        interrupted = True

    prev_sigint = signal.signal(signal.SIGINT, handle_signal)
    prev_sigterm = signal.signal(signal.SIGTERM, handle_signal)

    try:
        # 1. Turn ON valves
        print("[ACTION] Turning ON all selected water valves...")
        if test_main:
            _set_main_valve(True)
        if test_cooling:
            _set_cooling_valve(True)
        if test_heating:
            _set_heating_valve(True)

        print("\n>>> All selected valves are now OPEN (Active). <<<\n")

        # 2. Countdown loop
        start_time = time.time()
        while not interrupted:
            elapsed = time.time() - start_time
            remaining = duration - elapsed
            if remaining <= 0:
                break

            # Print live progress
            msg = f"\r[RUNNING] Valves ON... Time remaining: {remaining:5.1f}s / {duration:.1f}s  [Press Ctrl+C to abort] "
            sys.stdout.write(msg)
            sys.stdout.flush()

            sleep_step = min(0.1, remaining)
            time.sleep(sleep_step)

        if not interrupted:
            sys.stdout.write(f"\r[RUNNING] Valves ON... Time remaining:   0.0s / {duration:.1f}s  [Complete]            \n")
            sys.stdout.flush()

    finally:
        # 3. Always turn OFF valves in finally block for safety
        print("\n[ACTION] Turning OFF (Closing) all water valves...")
        if test_main:
            _set_main_valve(False)
        if test_cooling:
            _set_cooling_valve(False)
        if test_heating:
            _set_heating_valve(False)

        # Restore original signal handlers
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)

        print(">>> All valves successfully CLOSED (Idle). <<<\n")
        print("=" * 65)
        print("Test finished successfully.")
        print("=" * 65 + "\n")


def main():
    args = parse_args()
    duration = get_target_duration(args)

    # Determine which valves to test
    has_specific_filter = args.cooling_only or args.heating_only or args.main_only
    if has_specific_filter:
        test_cooling = args.cooling_only
        test_heating = args.heating_only
        test_main = args.main_only
    else:
        # Default: test all three
        test_cooling = True
        test_heating = True
        test_main = True

    run_valves_test(
        duration=duration,
        test_cooling=test_cooling,
        test_heating=test_heating,
        test_main=test_main,
    )


if __name__ == "__main__":
    main()
