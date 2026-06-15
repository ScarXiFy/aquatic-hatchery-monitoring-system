sourceValvePin = 18
drainValvePin = 16

isRpiPresent: bool = True
try:
    import RPi.GPIO as GPIO
except:
    isRpiPresent = False
    print("RPi module not present...")

sourceValveOpen = False
drainValveOpen = False

def setDummySourceValveState(isValveOpen: bool):
    global sourceValveOpen
    sourceValveOpen = isValveOpen
    print(f"[DUMMY] source valve -> {sourceValveOpen}")

def setDummyDrainValveState(isValveOpen: bool):
    global drainValveOpen
    drainValveOpen = isValveOpen
    print(f"[DUMMY] drain valve -> {drainValveOpen}")

# default assignments (PC mode)
sourceValveState = setDummySourceValveState
drainValveState = setDummyDrainValveState

# RPI IMPLEMENTATION
if isRpiPresent:
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(sourceValvePin, GPIO.OUT)
    GPIO.setup(drainValvePin, GPIO.OUT)

    def setSourceValveState(isValveOpen: bool):
        global sourceValveOpen
        sourceValveOpen = isValveOpen

        print(f"[RPI] source valve -> {sourceValveOpen}")
        GPIO.output(
            sourceValvePin,
            GPIO.HIGH if sourceValveOpen else GPIO.LOW
        )

    def setDrainValveState(isValveOpen: bool):
        global drainValveOpen
        drainValveOpen = isValveOpen

        print(f"[RPI] drain valve -> {drainValveOpen}")
        GPIO.output(
            drainValvePin,
            GPIO.HIGH if drainValveOpen else GPIO.LOW
        )

    # override dummy functions
    sourceValveState = setSourceValveState
    drainValveState = setDrainValveState