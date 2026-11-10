#!/usr/bin/env python3
"""
Dedicated Standalone Aerator Solenoids & Cooler Fan Test Script

Tests strictly the 4 aerator components:
  1. Aerator Solenoid Valve 1 : GPIO 10
  2. Aerator Solenoid Valve 2 : GPIO 9
  3. Aerator Solenoid Valve 3 : GPIO 11
  4. Aerator Fan              : GPIO 14

Logic (Active-Low Relay):
  - Solenoids : Active LOW (GPIO.LOW = OPEN/ON, GPIO.HIGH = CLOSED/OFF)
  - Cooler Fan: Active LOW (GPIO.LOW = ON, GPIO.HIGH = OFF)
  - Fan Rule  : Fan is ON whenever at least ONE solenoid is ON; OFF when all 3 are OFF.

Test Progression:
  Step 0: All OFF                   -> Fan: OFF
  Step 1: Solenoid 1 ON             -> Fan: ON
  Step 2: Solenoid 1 + 2 ON         -> Fan: ON
  Step 3: Solenoid 1 + 2 + 3 ON     -> Fan: ON
  Step 4: Solenoid 3 OFF (1 & 2 ON) -> Fan: ON
  Step 5: Solenoid 2 OFF (1 ON)     -> Fan: ON
  Step 6: Solenoid 1 OFF (All OFF)  -> Fan: OFF

Usage:
  python test_aerator_valves.py 3             # 3 seconds per step
  python test_aerator_valves.py --step 2      # 2 seconds per step
  python test_aerator_valves.py               # Prompts interactively for step duration
"""

import argparse
import signal
import sys
import time

# Pin Definitions (strictly these 4 pins only)
SOLENOID_PIN_1 = 10
SOLENOID_PIN_2 = 9
SOLENOID_PIN_3 = 11
FAN_PIN = 14

SOLENOID_PINS = [SOLENOID_PIN_1, SOLENOID_PIN_2, SOLENOID_PIN_3]
ALL_PINS = [SOLENOID_PIN_1, SOLENOID_PIN_2, SOLENOID_PIN_3, FAN_PIN]

DEFAULT_STEP_DURATION = 3.0

# Hardware availability check
isRpiPresent = True
try:
    import RPi.GPIO as GPIO
except ImportError:
    isRpiPresent = False
    GPIO = None

# Internal state tracking
solenoid_states = {
    SOLENOID_PIN_1: False,
    SOLENOID_PIN_2: False,
    SOLENOID_PIN_3: False,
}
fan_state = False


def setup_gpio():
    """Initializes strictly the 4 pins to outputs, defaulting to OFF/CLOSED (HIGH)."""
    if isRpiPresent and GPIO is not None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in ALL_PINS:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)


def _set_pin(pin: int, is_active: bool):
    """Sets a pin state on hardware if present (active-LOW: LOW=ON, HIGH=OFF)."""
    if isRpiPresent and GPIO is not None:
        GPIO.output(pin, GPIO.LOW if is_active else GPIO.HIGH)


def update_fan():
    """
    Enables fan if at least one solenoid is active, otherwise disables fan.
    Returns the new fan state.
    """
    global fan_state
    any_solenoid_on = any(solenoid_states.values())
    fan_state = any_solenoid_on
    _set_pin(FAN_PIN, fan_state)
    return fan_state


def set_solenoids(s1: bool, s2: bool, s3: bool):
    """
    Sets the 3 solenoid states and automatically updates the fan to match.
    """
    solenoid_states[SOLENOID_PIN_1] = bool(s1)
    solenoid_states[SOLENOID_PIN_2] = bool(s2)
    solenoid_states[SOLENOID_PIN_3] = bool(s3)

    _set_pin(SOLENOID_PIN_1, s1)
    _set_pin(SOLENOID_PIN_2, s2)
    _set_pin(SOLENOID_PIN_3, s3)

    update_fan()


def all_off():
    """Turns off all 3 solenoids and the fan."""
    set_solenoids(False, False, False)


def cleanup():
    """Safely turns off all 4 components and cleans up strictly their 4 pins."""
    all_off()
    if isRpiPresent and GPIO is not None:
        try:
            GPIO.cleanup(ALL_PINS)
        except Exception:
            pass


def print_status_table(step_num: int, total_steps: int, description: str):
    """Prints a clear, formatted status panel for the 4 components."""
    s1_str = "ON " if solenoid_states[SOLENOID_PIN_1] else "OFF"
    s2_str = "ON " if solenoid_states[SOLENOID_PIN_2] else "OFF"
    s3_str = "ON " if solenoid_states[SOLENOID_PIN_3] else "OFF"
    fan_str = "ON " if fan_state else "OFF"

    active_count = sum(solenoid_states.values())

    print(f"\n[STEP {step_num}/{total_steps}] {description}")
    print("┌───────────────────────────────────┬────────┐")
    print(f"│ Solenoid Valve 1  (GPIO {SOLENOID_PIN_1:<2})        │  {s1_str}   │")
    print(f"│ Solenoid Valve 2  (GPIO {SOLENOID_PIN_2:<2})        │  {s2_str}   │")
    print(f"│ Solenoid Valve 3  (GPIO {SOLENOID_PIN_3:<2})        │  {s3_str}   │")
    print("├───────────────────────────────────┼────────┤")
    print(f"│ Cooler Fan        (GPIO {FAN_PIN:<2})        │  {fan_str}   │  ({active_count}/3 solenoids active)")
    print("└───────────────────────────────────┴────────┘")


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Test strictly the 3 aerator solenoid valves and cooler fan."
    )
    parser.add_argument(
        "duration_pos",
        nargs="?",
        type=float,
        default=None,
        help="Duration in seconds per step (e.g. 3.0)",
    )
    parser.add_argument(
        "-s",
        "--step",
        dest="step_duration",
        type=float,
        default=None,
        help=f"Duration in seconds per step (default: {DEFAULT_STEP_DURATION}s)",
    )
    return parser.parse_args(args)


def get_step_duration(args) -> float:
    if args.duration_pos is not None:
        duration = args.duration_pos
    elif args.step_duration is not None:
        duration = args.step_duration
    else:
        if sys.stdin.isatty():
            try:
                user_input = input(f"Enter duration in seconds per step [{DEFAULT_STEP_DURATION}]: ").strip()
                if not user_input:
                    duration = DEFAULT_STEP_DURATION
                else:
                    duration = float(user_input)
            except (ValueError, EOFError):
                print(f"\n[INFO] Invalid input. Using default step duration of {DEFAULT_STEP_DURATION}s.")
                duration = DEFAULT_STEP_DURATION
        else:
            duration = DEFAULT_STEP_DURATION

    if duration <= 0:
        print("[WARNING] Step duration must be > 0. Using default of 1.0s.")
        duration = 1.0

    return duration


def run_countdown(step_duration: float, interrupted_check):
    """Performs live countdown in terminal for the current step."""
    start_time = time.time()
    while not interrupted_check():
        elapsed = time.time() - start_time
        remaining = step_duration - elapsed
        if remaining <= 0:
            break

        msg = f"\r  Remaining in step: {remaining:4.1f}s / {step_duration:.1f}s  [Press Ctrl+C to abort] "
        sys.stdout.write(msg)
        sys.stdout.flush()

        time.sleep(min(0.1, remaining))

    if not interrupted_check():
        sys.stdout.write(f"\r  Remaining in step:   0.0s / {step_duration:.1f}s  [Step Complete]         \n")
        sys.stdout.flush()


def run_aerator_test(step_duration: float):
    """
    Executes staged turn-on and turn-off sequence for the 3 aerator solenoids,
    verifying fan responsiveness at each stage.
    """
    mode_str = "Raspberry Pi Hardware (GPIO)" if isRpiPresent else "Simulation Mode (Dummy)"

    print("\n" + "=" * 65)
    print("     AERATOR SOLENOID VALVES & COOLER FAN TEST BENCH")
    print("=" * 65)
    print(f"Mode              : {mode_str}")
    print(f"Step Hold Time    : {step_duration:.1f} second(s) per step")
    print("Components Tested (ONLY these 4 pins):")
    print(f"  • Solenoid 1 : GPIO {SOLENOID_PIN_1}")
    print(f"  • Solenoid 2 : GPIO {SOLENOID_PIN_2}")
    print(f"  • Solenoid 3 : GPIO {SOLENOID_PIN_3}")
    print(f"  • Cooler Fan : GPIO {FAN_PIN}")
    print("Isolated Systems (NOT energized/touched):")
    print("  • Cooling water valve & peltiers : OFF")
    print("  • Heating water valve & relay    : OFF")
    print("  • Main water valve & mixer       : OFF")
    print("=" * 65)

    setup_gpio()

    interrupted = False

    def handle_signal(sig, frame):
        nonlocal interrupted
        print("\n\n[WARNING] Interrupt received! Immediately shutting off solenoids and fan...")
        interrupted = True

    prev_sigint = signal.signal(signal.SIGINT, handle_signal)
    prev_sigterm = signal.signal(signal.SIGTERM, handle_signal)

    # Sequence of (Solenoid 1, Solenoid 2, Solenoid 3, Step Description, Expected Fan State)
    test_steps = [
        (True, False, False, "Turn ON Solenoid 1 -> Fan should turn ON", True),
        (True, True, False, "Turn ON Solenoid 2 (1+2 active) -> Fan remains ON", True),
        (True, True, True, "Turn ON Solenoid 3 (All 3 active) -> Fan remains ON", True),
        (True, True, False, "Turn OFF Solenoid 3 (1+2 active) -> Fan remains ON", True),
        (True, False, False, "Turn OFF Solenoid 2 (1 active) -> Fan remains ON", True),
        (False, False, False, "Turn OFF Solenoid 1 (0 active) -> Fan should turn OFF", False),
    ]

    try:
        total = len(test_steps)
        for idx, (s1, s2, s3, desc, expected_fan) in enumerate(test_steps, start=1):
            if interrupted:
                break

            set_solenoids(s1, s2, s3)
            assert fan_state == expected_fan, f"Fan state mismatch: expected {expected_fan}, got {fan_state}"
            print_status_table(idx, total, desc)

            run_countdown(step_duration, lambda: interrupted)

    finally:
        print("\n[ACTION] Resetting and closing all 4 components...")
        cleanup()

        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)

        print(">>> All 3 aerator solenoids and fan are CLOSED / OFF (Idle). <<<\n")
        print("=" * 65)
        print("Aerator test finished.")
        print("=" * 65 + "\n")


def main():
    args = parse_args()
    step_duration = get_step_duration(args)
    run_aerator_test(step_duration)


if __name__ == "__main__":
    main()
