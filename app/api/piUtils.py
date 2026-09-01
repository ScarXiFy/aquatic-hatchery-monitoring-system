import random
import time
import logging

# TEMPORARY PIN PLACEMENTS
sourceValvePin = 6
drainValvePin = 5

# Temperature control pins
#coolingSystemPin = 5
coolingValvePin = 24
heatingSystemPin = 19
heatingValvePin = 23
pel1Pin = 25
pel2Pin = 8
pel3Pin = 7
pel4Pin = 1
fan1Pin = 12
fan2Pin = 16
fan3Pin = 20
fan4Pin = 21

# Dissolved oxygen control pins
doSolenoidValve1 = 17         # NEED TO CONFIG FOR 3 PINS
doSolenoidValve2 = 27
doSolenoidValve3 = 22

# Stepper motor pins (Wantai 42bygh610-1 for bleed valve)
bleedValveDirPin = 10
bleedValveStepPin = 9

# Stepper motor configuration parameters
STEP_DELAY = 0.001          # 1 ms pulse spacing
STEPS_PER_POSITION = 18     # ~33 degree rotation per position (0%, 33%, 66%, 100%)
BLEED_VALVE_POSITIONS = [0.0, 33.0, 66.0, 100.0]

isRpiPresent: bool = True
try:
    import RPi.GPIO as GPIO
except:
    isRpiPresent = False
    GPIO = None
    print("RPi module not present...")

CW = GPIO.HIGH if isRpiPresent else 1
CCW = GPIO.LOW if isRpiPresent else 0

sourceValveOpen = False
drainValveOpen = False

coolingSystemActive = False
coolingValveOpen = False
heatingSystemActive = False
heatingValveOpen = False

doSolenoidValveOpen = False
doBleedValvePercent = 0  # 0-100 %
_dosvcounter = 3
motorInit: bool = False

# DUMMY implementations (PC)

def setDummySourceValveState(isValveOpen: bool):
    global sourceValveOpen
    sourceValveOpen = isValveOpen
    print(f"[DUMMY] source valve -> {sourceValveOpen}")

def setDummyDrainValveState(isValveOpen: bool):
    global drainValveOpen
    drainValveOpen = isValveOpen
    print(f"[DUMMY] drain valve -> {drainValveOpen}")

# Temperature
def setDummyCoolingSystem(active: bool):
    global coolingSystemActive
    coolingSystemActive = active
    print(f"[DUMMY] cooling system -> {'ON' if active else 'OFF'}")

def setDummyCoolingValve(isOpen: bool):
    global coolingValveOpen
    coolingValveOpen = isOpen
    print(f"[DUMMY] cooling valve -> {'OPEN' if isOpen else 'CLOSED'}")

def setDummyHeatingSystem(active: bool):
    global heatingSystemActive
    heatingSystemActive = active
    print(f"[DUMMY] heating system -> {'ON' if active else 'OFF'}")

def setDummyHeatingValve(isOpen: bool):
    global heatingValveOpen
    heatingValveOpen = isOpen
    print(f"[DUMMY] heating valve -> {'OPEN' if isOpen else 'CLOSED'}")

# Dissolved oxygen
def setDummyDoSolenoidValve(isOpen: bool):
    global _dosvcounter
    if isOpen:
        _dosvcounter += 1
    else:
        _dosvcounter -= 1
    _dosvcounter = max(0, min(3, _dosvcounter))
    print(f"[DUMMY] DO solenoid valves open: {_dosvcounter}/3")

def setDummyDoBleedValve(percent: float):
    global doBleedValvePercent
    doBleedValvePercent = max(0.0, min(100.0, percent))
    print(f"[DUMMY] DO bleed valve (stepper) -> {doBleedValvePercent:.1f}%")


def initializeBleedValveMotor(app=None):
    """
    Initialize bleed valve stepper motor on startup. Reads stored motor position from DB,
    resets motor CCW to position 0 (fully closed) if needed, sets motorInit = True, and
    saves position 0 in DB.
    """
    global motorInit, doBleedValvePercent

    def _do_init():
        global motorInit, doBleedValvePercent
        from app.models import load_motor_state, save_motor_state

        saved_pos = load_motor_state("bleed_valve")
        if saved_pos is None or not isinstance(saved_pos, int):
            saved_pos = 0

        saved_pos = max(0, min(3, saved_pos))

        if saved_pos > 0:
            total_steps = saved_pos * STEPS_PER_POSITION
            if isRpiPresent and GPIO is not None:
                print(f"[RPI] Realigning bleed valve motor from stored position {saved_pos} to 0 ({total_steps} steps CW)...")
                try:
                    GPIO.output(bleedValveDirPin, CW)
                    for _ in range(total_steps):
                        GPIO.output(bleedValveStepPin, GPIO.HIGH)
                        time.sleep(STEP_DELAY)
                        GPIO.output(bleedValveStepPin, GPIO.LOW)
                        time.sleep(STEP_DELAY)
                except Exception as e:
                    logging.warning(f"[RPI] GPIO exception during motor init reset: {e}")
                    print(f"[RPI] GPIO exception during motor init reset: {e}")
            else:
                print(f"[DUMMY] Realigning bleed valve motor from stored position {saved_pos} to 0 ({total_steps} steps CW)...")

        doBleedValvePercent = 0.0
        try:
            save_motor_state("bleed_valve", 0)
        except Exception as e:
            logging.warning(f"Failed to save motor state during initialization: {e}")

        motorInit = True
        prefix = "[RPI]" if isRpiPresent else "[DUMMY]"
        print(f"{prefix} Bleed valve motor initialized to position 0 (fully closed)")

    if app is not None:
        with app.app_context():
            _do_init()
    else:
        try:
            from flask import current_app
            if current_app:
                _do_init()
            else:
                _do_init()
        except Exception:
            _do_init()


def setDoBleedValveMotor(target_percent: float):
    """
    Control Wantai 42bygh610-1 stepper motor for the bleed valve across 4 discrete positions:
    0%, 33%, 66%, and 100%.
    """
    global doBleedValvePercent

    if not motorInit:
        logging.critical("[RPI] Bleed valve motor not initialized! Movement skipped.")
        print("[RPI] CRITICAL: Bleed valve motor not initialized! Movement skipped.")
        return

    positions = BLEED_VALVE_POSITIONS
    target_percent_clamped = max(0.0, min(100.0, target_percent))
    target_level = min(range(len(positions)), key=lambda i: abs(positions[i] - target_percent_clamped))

    if not isRpiPresent:
        setDummyDoBleedValve(target_percent)
        try:
            from app.models import save_motor_state
            save_motor_state("bleed_valve", target_level)
        except Exception:
            pass
        return

    current_level = min(range(len(positions)), key=lambda i: abs(positions[i] - doBleedValvePercent))

    if current_level == target_level:
        print(f"[RPI] Bleed valve already at target position ({positions[target_level]:.1f}%)")
        return

    level_diff = target_level - current_level
    direction = CCW if level_diff > 0 else CW
    num_positions = abs(level_diff)
    total_steps = num_positions * STEPS_PER_POSITION

    print(f"[RPI] Bleed valve moving to {target_percent}%")

    try:
        GPIO.output(bleedValveDirPin, direction)
        for _ in range(total_steps):
            GPIO.output(bleedValveStepPin, GPIO.HIGH)
            time.sleep(STEP_DELAY)
            GPIO.output(bleedValveStepPin, GPIO.LOW)
            time.sleep(STEP_DELAY)

        doBleedValvePercent = positions[target_level]
        try:
            from app.models import save_motor_state
            save_motor_state("bleed_valve", target_level)
        except Exception as e:
            logging.warning(f"Failed to persist motor state: {e}")
        print(f"[RPI] Bleed valve reached {doBleedValvePercent:.1f}%")
    except Exception as e:
        logging.error(f"[RPI] GPIO failure during bleed valve motor movement: {e}")
        print(f"[RPI] GPIO failure during bleed valve motor movement: {e}")



# default assignments (PC mode)
sourceValveState = setDummySourceValveState
drainValveState = setDummyDrainValveState

coolingSystem = setDummyCoolingSystem
coolingValve = setDummyCoolingValve
heatingSystem = setDummyHeatingSystem
heatingValve = setDummyHeatingValve

doSolenoidValve = setDummyDoSolenoidValve
doBleedValve = setDummyDoBleedValve

# RPi implementations (GPIO mode)
if isRpiPresent:
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(sourceValvePin, GPIO.OUT)
    GPIO.setup(drainValvePin, GPIO.OUT)
    GPIO.setup(pel1Pin, GPIO.OUT)
    GPIO.setup(pel2Pin, GPIO.OUT)
    GPIO.setup(pel3Pin, GPIO.OUT)
    GPIO.setup(pel4Pin, GPIO.OUT)
    GPIO.setup(fan1Pin, GPIO.OUT)
    GPIO.setup(fan2Pin, GPIO.OUT)
    GPIO.setup(fan3Pin, GPIO.OUT)
    GPIO.setup(fan4Pin, GPIO.OUT)
    GPIO.setup(coolingValvePin, GPIO.OUT)
    GPIO.setup(heatingSystemPin, GPIO.OUT)
    GPIO.setup(heatingValvePin, GPIO.OUT)
    GPIO.setup(doSolenoidValve1, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(doSolenoidValve2, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(doSolenoidValve3, GPIO.OUT, initial=GPIO.HIGH)

    # Stepper motor (Wantai 42bygh610-1) setup for bleed valve
    GPIO.setup(bleedValveDirPin, GPIO.OUT)
    GPIO.setup(bleedValveStepPin, GPIO.OUT)

    def setSourceValveState(isValveOpen: bool):
        global sourceValveOpen
        sourceValveOpen = isValveOpen
        print(f"[RPI] source valve -> {sourceValveOpen}")
        GPIO.output(sourceValvePin, GPIO.HIGH if sourceValveOpen else GPIO.LOW)

    def setDrainValveState(isValveOpen: bool):
        global drainValveOpen
        drainValveOpen = isValveOpen
        print(f"[RPI] drain valve -> {drainValveOpen}")
        GPIO.output(drainValvePin, GPIO.HIGH if drainValveOpen else GPIO.LOW)

    def setCoolingSystem(active: bool):
        global coolingSystemActive
        coolingSystemActive = active
        print(f"[RPI] cooling system -> {'ON' if active else 'OFF'}")
        GPIO.output(pel1Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(pel2Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(pel3Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(pel4Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(fan1Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(fan2Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(fan3Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(fan4Pin, GPIO.LOW if active else GPIO.HIGH)

    def setCoolingValve(isOpen: bool):
        global coolingValveOpen
        coolingValveOpen = isOpen
        print(f"[RPI] cooling valve -> {'OPEN' if isOpen else 'CLOSED'}")
        GPIO.output(coolingValvePin, GPIO.HIGH if isOpen else GPIO.LOW)

    def setHeatingSystem(active: bool):
        global heatingSystemActive
        heatingSystemActive = active
        print(f"[RPI] heating system -> {'ON' if active else 'OFF'}")
        GPIO.output(heatingSystemPin, GPIO.HIGH if active else GPIO.LOW)

    def setHeatingValve(isOpen: bool):
        global heatingValveOpen
        heatingValveOpen = isOpen
        print(f"[RPI] heating valve -> {'OPEN' if isOpen else 'CLOSED'}")
        GPIO.output(heatingValvePin, GPIO.HIGH if isOpen else GPIO.LOW)

    def setDoSolenoidValve(isOpen: bool):
        global doSolenoidValveOpen
        doSolenoidValveOpen = isOpen
        global _dosvcounter
        if isOpen is True:
            _dosvcounter+=1
        else:
            _dosvcounter-=1
        print(f"[RPI] DO solenoid valve -> {isOpen}")
        if _dosvcounter > 3:
            print(f"[RPI] MAX valves OPEN -> {_dosvcounter}")
            _dosvcounter = 3
        elif _dosvcounter < 0:
            print(f"[RPI] ALL valves CLOSED -> {_dosvcounter}")
            _dosvcounter = 0

        print(f"[RPI] DO solenoid valves open: {_dosvcounter}/3")

        if _dosvcounter == 3:
            GPIO.output(doSolenoidValve1, GPIO.HIGH)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
            GPIO.output(doSolenoidValve3, GPIO.HIGH)
        elif _dosvcounter == 2:
            GPIO.output(doSolenoidValve1, GPIO.HIGH)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
            GPIO.output(doSolenoidValve3, GPIO.LOW)
        elif _dosvcounter == 1:
            GPIO.output(doSolenoidValve1, GPIO.HIGH)
            GPIO.output(doSolenoidValve2, GPIO.LOW)
            GPIO.output(doSolenoidValve3, GPIO.LOW)
        elif _dosvcounter == 0:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.LOW)
            GPIO.output(doSolenoidValve3, GPIO.LOW)

    # override with RPi implementations
    sourceValveState = setSourceValveState
    drainValveState = setDrainValveState
    coolingSystem = setCoolingSystem
    coolingValve = setCoolingValve
    heatingSystem = setHeatingSystem
    heatingValve = setHeatingValve
    doSolenoidValve = setDoSolenoidValve
    doBleedValve = setDoBleedValveMotor


# -----------------------Control logic-----------------------
# Default tolerances:
#   Temperature      : +-0.5 °C
#   Dissolved Oxygen : +-0.3 mg/L

TEMP_TOLERANCE = 0.5   # degC either side of setpoint
DO_TOLERANCE   = 0.3   # mg/L either side of setpoint


def applyTemperatureControl(temperature: float, setpoint: float, tolerance: float = TEMP_TOLERANCE, secondary_temp: float = None):
    """
    Uses the main tank reading to detect whether a threshold has been crossed.
    Once triggered, uses the Mixing sensor reading (heater/chiller output line)
    to drive the heating/cooling actuators. Falls back to a simulated Mixing tank temp
    value if the Mixing sensor is not connected.

    High temp  (tank > setpoint + tolerance) -> enable cooling system + open cooling valve.
    Low temp   (tank < setpoint - tolerance) -> enable heating system + open heating valve.
    Stable     (within tolerance band)       -> disable both systems and close both valves.
    """
    high = setpoint + tolerance
    low  = setpoint - tolerance

    # Resolve secondary temperature: use provided value, then try sensor, then simulate
    if secondary_temp is None:
        secondary_temp = read_secondary_temp_sensor()
    if secondary_temp is None:
        secondary_temp = round(temperature + random.uniform(-0.5, 0.5), 2)
        print(f"[DUMMY] Mixing temperature simulated as {secondary_temp}C")

    if temperature > high:
        if secondary_temp > high:
            print(f"[CONTROL] Tank temp HIGH ({temperature}C > {high}C) | Mixing: {secondary_temp}C - activating cooling")
            heatingSystem(False)
            heatingValve(False)
            coolingSystem(True)
            coolingValve(True)
        elif secondary_temp < low:
            print(f"[CONTROL] Tank temp HIGH ({temperature}C > {high}C) | Mixing: {secondary_temp}C - activating heating")
            coolingSystem(False)
            coolingValve(False)
            heatingSystem(True)
            heatingValve(True)

    elif temperature < low:
        if secondary_temp > high:
            print(f"[CONTROL] Tank temp LOW ({temperature}C < {low}C) | Mixing: {secondary_temp}C - activating cooling")
            heatingSystem(False)
            heatingValve(False)
            coolingSystem(True)
            coolingValve(True)
        elif secondary_temp < low:
            print(f"[CONTROL] Tank temp LOW ({temperature}C < {low}C) | Mixing: {secondary_temp}C - activating heating")
            coolingSystem(False)
            coolingValve(False)
            heatingSystem(True)
            heatingValve(True)

    else:
        print(f"[CONTROL] Main Tank temp STABLE ({temperature}C within +-{tolerance}C of setpoint {setpoint}C) - all systems idle")
        coolingSystem(False)
        coolingValve(False)
        heatingSystem(False)
        heatingValve(False)


def applyDissolvedOxygenControl(do_level: float, setpoint: float, tolerance: float = DO_TOLERANCE):
    """
    High DO (reading > setpoint + tolerance) -> close solenoid valve, open bleed valve (stepper) by 33%.
    Low DO  (reading < setpoint - tolerance) -> open solenoid valve, close bleed valve (stepper) by 33%.
    Stable  (within tolerance band)          -> retain current state
    """
    high = setpoint + tolerance
    low  = setpoint - tolerance

    if do_level > high:
        print(f"[CONTROL] DO HIGH ({do_level} mg/L > {high} mg/L, setpoint {setpoint} mg/L) - closing solenoid, opening bleed 33%")
        doSolenoidValve(False)
        doBleedValve(doBleedValvePercent + 33.0)

    elif do_level < low:
        print(f"[CONTROL] DO LOW ({do_level} mg/L < {low} mg/L, setpoint {setpoint} mg/L) - opening solenoid, closing bleed 33%")
        doSolenoidValve(True)
        doBleedValve(max(0.0, doBleedValvePercent - 33.0))

    else:
        print(f"[CONTROL] DO STABLE ({do_level} mg/L within +-{tolerance} mg/L of setpoint {setpoint} mg/L) - solenoid {doSolenoidValveOpen}, bleed valve {doBleedValvePercent}")
        #doSolenoidValve(doSolenoidValveOpen)
        #doBleedValve(doBleedValvePercent)


# ---------------------------------------------------------------------------
# DS18B20 temperature sensor (1-Wire, RPi only)
# ---------------------------------------------------------------------------

_temp_device_file = None
_temp_secondary_device_file = None

if isRpiPresent:
    import os as _os
    import glob as _glob

    _os.system("modprobe w1-gpio")
    _os.system("modprobe w1-therm")

    _base_dir = "/sys/bus/w1/devices/"
    _temp_device_file = _base_dir + "28-000000b197bd" + "/w1_slave"
    _temp_secondary_device_file = _base_dir + "28-000000b260f4" + "/w1_slave"
    if _os.path.exists(_temp_device_file):
        print(f"[RPI] DS18B20 Main tank sensor found: {_temp_device_file}")
    else:
        _temp_device_file = None
        print("[RPI] DS18B20 Main tank sensor not found - Main tank temperature will fall back to simulation")

    if _os.path.exists(_temp_secondary_device_file):
        print(f"[RPI] DS18B20 Mixing sensor found: {_temp_secondary_device_file}")
    else:
        _temp_secondary_device_file = None
        print("[RPI] DS18B20 Mixing sensor not found - Mixing temperature will use simulated value")


def _read_temp_raw():
    with open(_temp_device_file, "r") as f:
        return f.readlines()


def _parse_ds18b20(device_file: str, label: str):
    """Shared DS18B20 read-and-parse logic."""
    import time as _time
    with open(device_file, "r") as f:
        lines = f.readlines()
    retries = 0
    while lines[0].strip()[-3:] != "YES" and retries < 5:
        _time.sleep(0.2)
        with open(device_file, "r") as f:
            lines = f.readlines()
        retries += 1
    equals_pos = lines[1].find("t=")
    if equals_pos == -1:
        print(f"[RPI] {label} DS18B20 parse error - using simulated value")
        return None
    temp_c = float(lines[1][equals_pos + 2:]) / 1000.0
    print(f"[RPI] {label} DS18B20 reading: {temp_c:.2f}C")
    return round(temp_c, 2)


def read_temp_sensor():
    """
    Read temperature (C) from the DS18B20 1-Wire sensor.
    Returns a float on RPi when the sensor is present, or None otherwise (then use simulated value).
    """
    if not isRpiPresent or _temp_device_file is None:
        print("[DUMMY] Main Tank temperature sensor read - using simulated value")
        return None
    try:
        return _parse_ds18b20(_temp_device_file, "primary")
    except Exception as e:
        print(f"[RPI] Main Tank DS18B20 read failed ({e}) - using simulated value")
        return None


def read_secondary_temp_sensor():
    """
    Read temperature (C) from the Mixing tank DS18B20 1-Wire sensor.
    Returns a float on RPi when the sensor is present, or None otherwise (then use simulated value).
    """
    if not isRpiPresent or _temp_secondary_device_file is None:
        print("[DUMMY] Mixing tank temperature sensor read - using simulated value")
        return None
    try:
        return _parse_ds18b20(_temp_secondary_device_file, "secondary")
    except Exception as e:
        print(f"[RPI] Mixing tank DS18B20 read failed ({e}) - using simulated value")
        return None
