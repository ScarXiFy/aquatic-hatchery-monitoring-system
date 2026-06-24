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


def applyTemperatureControl(temperature: float, setpoint: float, tolerance: float = TEMP_TOLERANCE):
    """
    High temp  (reading > setpoint + tolerance) -> enable cooling system + open cooling valve.
    Low temp   (reading < setpoint - tolerance) -> enable heating system + open heating valve.
    Stable     (within tolerance band)          -> disable both systems and close both valves.
    """
    high = setpoint + tolerance
    low  = setpoint - tolerance

    if temperature > high:
        print(f"[CONTROL] Temperature HIGH ({temperature}C > {high}C, setpoint {setpoint}C) - activating cooling")
        heatingSystem(False)
        heatingValve(False)
        coolingSystem(True)
        coolingValve(True)

    elif temperature < low:
        print(f"[CONTROL] Temperature LOW ({temperature}C < {low}C, setpoint {setpoint}C) - activating heating")
        coolingSystem(False)
        coolingValve(False)
        heatingSystem(True)
        heatingValve(True)

    else:
        print(f"[CONTROL] Temperature STABLE ({temperature}C within +-{tolerance}C of setpoint {setpoint}C) - all systems idle")
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
