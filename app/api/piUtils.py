import random
# TEMPORARY PIN PLACEMENTS
sourceValvePin = 18
drainValvePin = 16

# Temperature control pins
coolingSystemPin = 20
coolingValvePin = 21
heatingSystemPin = 22
heatingValvePin = 23

# Dissolved oxygen control pins
doSolenoidValvePin = 24         # NEED TO CONFIG FOR 3 PINS
doBleedValveStepperPin = 25     # stepper motor (PWM duty cycle maps to open %)

isRpiPresent: bool = True
try:
    import RPi.GPIO as GPIO
except:
    isRpiPresent = False
    print("RPi module not present...")

sourceValveOpen = False
drainValveOpen = False

coolingSystemActive = False
coolingValveOpen = False
heatingSystemActive = False
heatingValveOpen = False

doSolenoidValveOpen = False
doBleedValvePercent = 0  # 0-100 %


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
    global doSolenoidValveOpen
    doSolenoidValveOpen = isOpen
    print(f"[DUMMY] DO solenoid valve -> {'OPEN' if isOpen else 'CLOSED'}")

def setDummyDoBleedValve(percent: float):
    global doBleedValvePercent
    doBleedValvePercent = max(0.0, min(100.0, percent))
    print(f"[DUMMY] DO bleed valve (stepper) -> {doBleedValvePercent:.1f}%")


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
    GPIO.setup(coolingSystemPin, GPIO.OUT)
    GPIO.setup(coolingValvePin, GPIO.OUT)
    GPIO.setup(heatingSystemPin, GPIO.OUT)
    GPIO.setup(heatingValvePin, GPIO.OUT)
    GPIO.setup(doSolenoidValvePin, GPIO.OUT)

    # Stepper motor driven via PWM on doBleedValveStepperPin
    GPIO.setup(doBleedValveStepperPin, GPIO.OUT)
    _bleed_pwm = GPIO.PWM(doBleedValveStepperPin, 50)  # 50 Hz
    _bleed_pwm.start(0)

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
        GPIO.output(coolingSystemPin, GPIO.HIGH if active else GPIO.LOW)

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
        print(f"[RPI] DO solenoid valve -> {'OPEN' if isOpen else 'CLOSED'}")
        GPIO.output(doSolenoidValvePin, GPIO.HIGH if isOpen else GPIO.LOW)

    def setDoBleedValve(percent: float):
        global doBleedValvePercent
        doBleedValvePercent = max(0.0, min(100.0, percent))
        print(f"[RPI] DO bleed valve (stepper) -> {doBleedValvePercent:.1f}%")
        _bleed_pwm.ChangeDutyCycle(doBleedValvePercent)

    # override with RPi implementations
    sourceValveState = setSourceValveState
    drainValveState = setDrainValveState
    coolingSystem = setCoolingSystem
    coolingValve = setCoolingValve
    heatingSystem = setHeatingSystem
    heatingValve = setHeatingValve
    doSolenoidValve = setDoSolenoidValve
    doBleedValve = setDoBleedValve


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
        doSolenoidValve(doSolenoidValveOpen)
        doBleedValve(doBleedValvePercent)


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
