#!/usr/bin/env python3
"""
Dedicated Standalone Water Valves Test Script:
Only controls the 3 water valves:
  1. Main Water Valve : GPIO 24
  2. Cooling Valve    : GPIO 25
  3. Heating Valve    : GPIO 23

IMPORTANT:
  Does NOT import or touch any other hardware or system:
  - No Cooling System (Peltier elements / Peltier fans)
  - No Heating System (Heater relay)
  - No Mixer motor
  - No Aerator solenoid valves or aerator cooler fan
  - No Stepper motor or LED PWM

Active-Low Relay Logic:
  - ON  (Open / Energized)  : GPIO.LOW
  - OFF (Closed / De-energized): GPIO.HIGH

Usage:
  python test_water_valves.py 5              # Turn on 3 valves for 5 seconds
  python test_water_valves.py --duration 10  # Turn on 3 valves for 10 seconds
  python test_water_valves.py                # Prompts interactively for duration
"""

import argparse
import signal
import sys
import time

# Pin Definitions (strictly the 3 water valves only)
MAIN_VALVE_PIN = 24
COOLING_VALVE_PIN = 25
HEATING_VALVE_PIN = 23

VALVE_PINS = [MAIN_VALVE_PIN, COOLING_VALVE_PIN, HEATING_VALVE_PIN]

DEFAULT_DURATION = 5.0

# Hardware availability check
isRpiPresent = True
try:
    import RPi.GPIO as GPIO
except ImportError:
    isRpiPresent = False
    GPIO = None

# Valve state tracking (for simulation / inspection)
valve_states = {
    MAIN_VALVE_PIN: False,
    COOLING_VALVE_PIN: False,
    HEATING_VALVE_PIN: False,
}


def setup_gpio():
    """Initializes strictly the 3 valve pins to outputs, defaulting to CLOSED (HIGH)."""
    if isRpiPresent and GPIO is not None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in VALVE_PINS:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)


def set_valve(pin: int, is_open: bool, valve_name: str = ""):
    """Sets a single valve state using active-low relay logic (LOW=ON, HIGH=OFF)."""
    valve_states[pin] = is_open
    state_str = "OPEN (ON)" if is_open else "CLOSED (OFF)"

    if isRpiPresent and GPIO is not None:
        GPIO.output(pin, GPIO.LOW if is_open else GPIO.HIGH)
        print(f"[RPI] {valve_name} (GPIO {pin}) -> {state_str}")
    else:
        print(f"[DUMMY] {valve_name} (GPIO {pin}) -> {state_str}")


def open_all_valves():
    """Opens ONLY the 3 water valves."""
    set_valve(MAIN_VALVE_PIN, True, "Main Water Valve")
    set_valve(COOLING_VALVE_PIN, True, "Cooling Valve")
    set_valve(HEATING_VALVE_PIN, True, "Heating Valve")


def close_all_valves():
    """Closes ONLY the 3 water valves."""
    set_valve(MAIN_VALVE_PIN, False, "Main Water Valve")
    set_valve(COOLING_VALVE_PIN, False, "Cooling Valve")
    set_valve(HEATING_VALVE_PIN, False, "Heating Valve")


def cleanup():
    """Safely closes all 3 valves and cleans up strictly their 3 pins."""
    close_all_valves()
    if isRpiPresent and GPIO is not None:
        try:
            GPIO.cleanup(VALVE_PINS)
        except Exception:
            pass


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Test strictly the 3 water valves (Cooling, Heating, Main) for a configurable duration."
    )
    parser.add_argument(
        "duration_pos",
        nargs="?",
        type=float,
        default=None,
        help="Duration in seconds to keep the 3 valves ON (e.g. 5.0)",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=None,
        help=f"Duration in seconds to keep the 3 valves ON (default: {DEFAULT_DURATION}s)",
    )
    return parser.parse_args(args)


def get_target_duration(args) -> float:
    if args.duration_pos is not None:
        duration = args.duration_pos
    elif args.duration is not None:
        duration = args.duration
    else:
        if sys.stdin.isatty():
            try:
                user_input = input(f"Enter duration in seconds to keep the 3 valves ON [{DEFAULT_DURATION}]: ").strip()
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
        print("[WARNING] Duration must be greater than 0. Using default of 1.0s.")
        duration = 1.0

    return duration


def run_valves_test(duration: float):
    """
    Turns on ONLY the cooling, heating, and main water valves for `duration` seconds,
    then safely closes them. No other system is energized or touched.
    """
    mode_str = "Raspberry Pi Hardware (GPIO)" if isRpiPresent else "Simulation Mode (Dummy)"

    print("\n" + "=" * 65)
    print("      WATER VALVES ONLY TEST (Cooling, Heating, Main)")
    print("=" * 65)
    print(f"Mode         : {mode_str}")
    print(f"Duration     : {duration:.1f} second(s)")
    print("Valves Under Test (ONLY these 3 pins):")
    print(f"  • Main Water Valve : GPIO {MAIN_VALVE_PIN}")
    print(f"  • Cooling Valve    : GPIO {COOLING_VALVE_PIN}")
    print(f"  • Heating Valve    : GPIO {HEATING_VALVE_PIN}")
    print("Isolated Systems (NOT energized/touched):")
    print("  • Cooling Peltier & fan : OFF")
    print("  • Heating relay         : OFF")
    print("  • Mixer motor           : OFF")
    print("  • Aerator & all others  : OFF")
    print("=" * 65 + "\n")

    setup_gpio()

    interrupted = False

    def handle_signal(sig, frame):
        nonlocal interrupted
        print("\n\n[WARNING] Interrupt received! Immediately closing all 3 valves...")
        interrupted = True

    prev_sigint = signal.signal(signal.SIGINT, handle_signal)
    prev_sigterm = signal.signal(signal.SIGTERM, handle_signal)

    try:
        print("[ACTION] Enabling ONLY the 3 water valves...")
        open_all_valves()
        print("\n>>> Main, Cooling, and Heating valves are now OPEN (Active). <<<\n")

        start_time = time.time()
        while not interrupted:
            elapsed = time.time() - start_time
            remaining = duration - elapsed
            if remaining <= 0:
                break

            msg = f"\r[RUNNING] 3 Valves ON... Time remaining: {remaining:5.1f}s / {duration:.1f}s  [Ctrl+C to stop] "
            sys.stdout.write(msg)
            sys.stdout.flush()

            sleep_step = min(0.1, remaining)
            time.sleep(sleep_step)

        if not interrupted:
            sys.stdout.write(f"\r[RUNNING] 3 Valves ON... Time remaining:   0.0s / {duration:.1f}s  [Complete]         \n")
            sys.stdout.flush()

    finally:
        print("\n[ACTION] Disabling (Closing) all 3 water valves...")
        cleanup()

        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)

        print(">>> Main, Cooling, and Heating valves are CLOSED (Idle). <<<\n")
        print("=" * 65)
        print("Test finished successfully.")
        print("=" * 65 + "\n")


def main():
    args = parse_args()
    duration = get_target_duration(args)
    run_valves_test(duration)


if __name__ == "__main__":
    main()
