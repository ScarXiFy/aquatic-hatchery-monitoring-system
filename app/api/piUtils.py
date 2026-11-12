import atexit
import random
import time
import logging
import threading

# Timing configuration for heating and mixing control
HEATER_PULSE_DURATION = 5.0       # Duration in seconds the heater stays ON per pulse
HEATER_CHECK_INTERVAL = 15.0      # Wait time in seconds between heating checks/pulses
MIXER_SHUTDOWN_DELAY = 10.0       # Run-on time in seconds for mixer after cooling/heating stops

# TEMPORARY PIN PLACEMENTS
sourceValvePin = 8
#drainValvePin = 5

# Temperature control pins
#coolingSystemPin = 5
coolingValvePin = 25
heatingSystemPin = 26
heatingValvePin = 23
mainValvePin = 24
pel1Pin = 12
pel2Pin = 16
pel3Pin = 20
pel4Pin = 21
fan1Pin = 1
fan2Pin = 15
#fan3Pin = 20
#fan4Pin = 21
mixerPin = 5
# LED Lighting PWM configuration (MOSFET driver)
ledPWMPin = 18
PWM_PIN = ledPWMPin
LED_PWM_FREQ = 1000          # 1000Hz frequency for flicker-free LED operation
MAX_LUX = 3000

# Dissolved oxygen control pins
doSolenoidValve1 = 10         # NEED TO CONFIG FOR 3 PINS
doSolenoidValve2 = 9
doSolenoidValve3 = 11
aeratorFanPin = 14            # Cooler fan for aerator solenoid valves
aeratorCoolerFanPin = aeratorFanPin

# Stepper motor pins (Wantai 42bygh610-1 for bleed valve)
bleedValveDirPin = 17
bleedValveStepPin = 27
bleedValveEnablePin = 22

# Stepper motor configuration parameters
STEP_DELAY = 0.001          # 1 ms pulse spacing
STEPS_PER_POSITION = 200     # ~33 degree rotation per position (0%, 33%, 66%, 100%)
BLEED_VALVE_POSITIONS = [0.0, 33.0, 66.0, 100.0]

isRpiPresent: bool = True
try:
    import RPi.GPIO as GPIO
except:
    isRpiPresent = False
    GPIO = None
    print("RPi module not present...")

isPigpioPresent: bool = True
try:
    import pigpio
except ImportError:
    isPigpioPresent = False
    pigpio = None

CW = GPIO.HIGH if isRpiPresent else 1
CCW = GPIO.LOW if isRpiPresent else 0
MOTOR_ENABLE = GPIO.LOW if isRpiPresent else 0
MOTOR_DISABLE = GPIO.HIGH if isRpiPresent else 1

sourceValveOpen = False
drainValveOpen = False
mainValveOpen = False

coolingSystemActive = False
coolingValveOpen = False
heatingSystemActive = False
heatingValveOpen = False
heaterActive = False
mixerActive = False
mixingFanActive = False
last_temp_system = "cooling"  # Tracks last active temperature system ("heating" or "cooling")
_heating_disabled_by_mixing = False
_cooling_disabled_by_mixing = False

# Synchronization for mixer and heater
_mixer_timer = None
_mixer_lock = threading.Lock()

_heating_thread = None
_heating_stop_event = threading.Event()
_heating_lock = threading.Lock()

doSolenoidValveOpen = False
doBleedValvePercent = 0  # 0-100 %
bleedValveDriverEnabled = False
_dosvcounter = 3
motorInit: bool = False
aeratorFanActive = True if _dosvcounter > 0 else False
aeratorCoolerFanActive = aeratorFanActive

currentLedLux = 0.0
currentLedDutyCycle = 0.0
pigpio_pi = None
ledPwm = None
mosfet = None

# LED Control functions

# Map requested target to an effective target that yields true lux
CALIBRATED_LUX = {
    0.0: 0.0,
    100.0: 180.0,   # Adjust this number until physical sensor reads 100
    500.0: 802.0,
    1000.0: 1580.0,
    3000.0: 3000.0,
}

def setDummyLux(target_lux: float):
    """
    Converts a target lux value into a PWM duty cycle (0 to 100)
    and simulates applying it to the MOSFET (PC mode).
    """
    global currentLedLux, currentLedDutyCycle
    try:
        target_lux = float(target_lux)
    except (TypeError, ValueError):
        target_lux = 0.0

    effective_target = CALIBRATED_LUX.get(target_lux, target_lux)
    duty_cycle = (effective_target / MAX_LUX) * 100.0
    duty_cycle = max(0.0, min(100.0, duty_cycle))
    currentLedLux = target_lux
    currentLedDutyCycle = duty_cycle
    print(f"[DUMMY] Lights set to {target_lux} lux ({duty_cycle:.1f}% power)")

setDummyLed = setDummyLux
setDummyLedIntensity = setDummyLux


def setRpiLux(target_lux: float):
    """
    Converts a target lux value into a PWM duty cycle (0 to 100)
    and applies it to the MOSFET using pigpio DMA PWM on PIN 18.
    Falls back to RPi.GPIO software PWM if pigpio daemon is unavailable.
    """
    global currentLedLux, currentLedDutyCycle, pigpio_pi, ledPwm, mosfet
    try:
        target_lux = float(target_lux)
    except (TypeError, ValueError):
        target_lux = 0.0

    effective_target = CALIBRATED_LUX.get(target_lux, target_lux)
    duty_cycle = (effective_target / MAX_LUX) * 100.0
    duty_cycle = max(0.0, min(100.0, duty_cycle))
    currentLedLux = target_lux
    currentLedDutyCycle = duty_cycle

    # Try initializing pigpio if available and not yet connected
    if pigpio_pi is None and isPigpioPresent and pigpio is not None:
        try:
            temp_pi = pigpio.pi()
            if getattr(temp_pi, "connected", False):
                pigpio_pi = temp_pi
                pigpio_pi.set_mode(ledPWMPin, pigpio.OUTPUT)
                pigpio_pi.set_PWM_frequency(ledPWMPin, LED_PWM_FREQ)
                pigpio_pi.set_PWM_range(ledPWMPin, 255)
                ledPwm = pigpio_pi
                mosfet = pigpio_pi
            else:
                logging.warning("[RPI] pigpiod daemon not connected. Run 'sudo pigpiod -t 1' for DMA PWM.")
        except Exception as e:
            logging.error(f"[RPI] Failed to initialize pigpio: {e}")

    if pigpio_pi is not None and getattr(pigpio_pi, "connected", True):
        try:
            pwm_val = int(round((duty_cycle / 100.0) * 255.0))
            pigpio_pi.set_PWM_dutycycle(ledPWMPin, pwm_val)
        except Exception as e:
            logging.error(f"[RPI] Failed to set pigpio DMA PWM duty cycle: {e}")
    elif isRpiPresent and GPIO is not None:
        if ledPwm is None:
            try:
                GPIO.setup(ledPWMPin, GPIO.OUT)
                ledPwm = GPIO.PWM(ledPWMPin, LED_PWM_FREQ)
                ledPwm.start(0)
                mosfet = ledPwm
            except Exception as e:
                logging.error(f"[RPI] Failed to initialize fallback LED PWM: {e}")
        if ledPwm is not None:
            try:
                ledPwm.ChangeDutyCycle(duty_cycle)
            except Exception as e:
                logging.error(f"[RPI] Failed to change fallback LED duty cycle: {e}")
    print(f"[RPI] Lights set to {target_lux} lux ({duty_cycle:.1f}% power)")

setRpiLed = setRpiLux
setRpiLedIntensity = setRpiLux


def set_lux(target_lux: float):
    """
    Converts target lux into duty cycle and applies it to LED PWM on PIN 18.
    Dispatches to setRpiLux if RPi is present, else setDummyLux.
    """
    if isRpiPresent:
        return setRpiLux(target_lux)
    else:
        return setDummyLux(target_lux)

setLedIntensity = set_lux
ledIntensity = set_lux

# DUMMY implementations (PC)

def setDummySourceValveState(isValveOpen: bool):
    global sourceValveOpen
    sourceValveOpen = isValveOpen
    print(f"[DUMMY] source valve -> {sourceValveOpen}")

def setDummyDrainValveState(isValveOpen: bool):
    global drainValveOpen
    drainValveOpen = isValveOpen
    print(f"[DUMMY] drain valve -> {drainValveOpen}")

def setDummyMainValve(isOpen: bool):
    global mainValveOpen
    mainValveOpen = isOpen
    print(f"[DUMMY] main water valve -> {'OPEN' if isOpen else 'CLOSED'}")

# Default main valve pointer (PC mode)
mainValve = setDummyMainValve
mainValveState = setDummyMainValve

def _update_main_valve_state():
    # Main valve is kept always ON for constant water circulation
    if not mainValveOpen:
        mainValve(True)

def setDummyMixer(active: bool):
    global mixerActive, mixingFanActive
    mixerActive = active
    mixingFanActive = active
    print(f"[DUMMY] mixer motor -> {'ON' if active else 'OFF'}")

def _setDummyHeaterRelay(active: bool):
    global heaterActive
    heaterActive = active
    print(f"[DUMMY] heater relay -> {'ON' if active else 'OFF'}")

_set_heater_relay = _setDummyHeaterRelay

def _update_mixer_state():
    global _mixer_timer
    with _mixer_lock:
        temp_system_active = (
            coolingSystemActive
            or heatingSystemActive
            or _heating_disabled_by_mixing
            or _cooling_disabled_by_mixing
        )
        if temp_system_active:
            if _mixer_timer is not None:
                _mixer_timer.cancel()
                _mixer_timer = None
            if not mixerActive:
                mixer(True)
        else:
            if mixerActive and _mixer_timer is None:
                def _delayed_mixer_off():
                    global _mixer_timer
                    with _mixer_lock:
                        _mixer_timer = None
                        if not (
                            coolingSystemActive
                            or heatingSystemActive
                            or _heating_disabled_by_mixing
                            or _cooling_disabled_by_mixing
                        ):
                            mixer(False)
                _mixer_timer = threading.Timer(MIXER_SHUTDOWN_DELAY, _delayed_mixer_off)
                _mixer_timer.daemon = True
                _mixer_timer.start()
                print(f"[CONTROL] Cooling/heating disabled - mixer will turn OFF in {MIXER_SHUTDOWN_DELAY}s")

def _heating_worker():
    while not _heating_stop_event.is_set() and heatingSystemActive:
        _set_heater_relay(True)
        if _heating_stop_event.wait(HEATER_PULSE_DURATION):
            _set_heater_relay(False)
            break
        _set_heater_relay(False)

        if _heating_stop_event.wait(HEATER_CHECK_INTERVAL):
            break

def setRpiMixer(active: bool):
    global mixerActive, mixingFanActive
    mixerActive = active
    mixingFanActive = active
    print(f"[RPI] mixer motor -> {'ON' if active else 'OFF'}")
    if isRpiPresent and GPIO is not None:
        GPIO.output(mixerPin, GPIO.LOW if active else GPIO.HIGH)

setMixer = setRpiMixer

def _setRpiHeaterRelay(active: bool):
    global heaterActive
    heaterActive = active
    print(f"[RPI] heater relay -> {'ON' if active else 'OFF'}")
    if isRpiPresent and GPIO is not None:
        GPIO.output(heatingSystemPin, GPIO.HIGH if active else GPIO.LOW)

def setRpiMainValve(isOpen: bool):
    global mainValveOpen
    mainValveOpen = isOpen
    print(f"[RPI] main water valve -> {'OPEN' if isOpen else 'CLOSED'}")
    if isRpiPresent and GPIO is not None:
        GPIO.output(mainValvePin, GPIO.LOW if isOpen else GPIO.HIGH)

setMainValve = setRpiMainValve

def setRpiSourceValveState(isValveOpen: bool):
    global sourceValveOpen
    sourceValveOpen = isValveOpen
    print(f"[RPI] source valve -> {sourceValveOpen}")
    if isRpiPresent and GPIO is not None:
        GPIO.output(sourceValvePin, GPIO.LOW if sourceValveOpen else GPIO.HIGH)

setSourceValve = setRpiSourceValveState

def setRpiDrainValveState(isValveOpen: bool):
    global drainValveOpen
    drainValveOpen = isValveOpen
    print(f"[RPI] drain valve -> {drainValveOpen}")
    #if isRpiPresent and GPIO is not None:
    #    GPIO.output(drainValvePin, GPIO.LOW if drainValveOpen else GPIO.HIGH)

setDrainValve = setRpiDrainValveState

def setRpiCoolingValve(isOpen: bool):
    global coolingValveOpen
    coolingValveOpen = isOpen
    print(f"[RPI] cooling valve -> {'OPEN' if isOpen else 'CLOSED'}")
    if isRpiPresent and GPIO is not None:
        GPIO.output(coolingValvePin, GPIO.LOW if isOpen else GPIO.HIGH)

setCoolingValve = setRpiCoolingValve

def setRpiHeatingValve(isOpen: bool):
    global heatingValveOpen
    heatingValveOpen = isOpen
    print(f"[RPI] heating valve -> {'OPEN' if isOpen else 'CLOSED'}")
    if isRpiPresent and GPIO is not None:
        GPIO.output(heatingValvePin, GPIO.LOW if isOpen else GPIO.HIGH)

setHeatingValve = setRpiHeatingValve

# Temperature
def setDummyCoolingSystem(active: bool):
    global coolingSystemActive
    coolingSystemActive = active
    print(f"[DUMMY] cooling system -> {'ON' if active else 'OFF'}")
    _update_mixer_state()
    _update_main_valve_state()

def setRpiCoolingSystem(active: bool):
    global coolingSystemActive
    coolingSystemActive = active
    if isRpiPresent and GPIO is not None:
        print(f"[RPI] cooling system -> {'ON' if active else 'OFF'}")
        GPIO.output(pel1Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(pel2Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(pel3Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(pel4Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(fan1Pin, GPIO.LOW if active else GPIO.HIGH)
        GPIO.output(fan2Pin, GPIO.LOW if active else GPIO.HIGH)
    else:
        print(f"[DUMMY] cooling system -> {'ON' if active else 'OFF'}")
    _update_mixer_state()
    _update_main_valve_state()

setCoolingSystem = setDummyCoolingSystem

def setDummyCoolingValve(isOpen: bool):
    global coolingValveOpen
    coolingValveOpen = isOpen
    print(f"[DUMMY] cooling valve -> {'OPEN' if isOpen else 'CLOSED'}")

def setDummyHeatingSystem(active: bool):
    global heatingSystemActive, _heating_thread
    with _heating_lock:
        heatingSystemActive = active
        print(f"[DUMMY] heating system -> {'ON' if active else 'OFF'}")
        if active:
            _update_mixer_state()
            _update_main_valve_state()
            if _heating_thread is None or not _heating_thread.is_alive():
                _heating_stop_event.clear()
                _heating_thread = threading.Thread(target=_heating_worker, daemon=True)
                _heating_thread.start()
        else:
            _heating_stop_event.set()
            _set_heater_relay(False)
            _heating_thread = None
            _update_mixer_state()
            _update_main_valve_state()

def setDummyHeatingValve(isOpen: bool):
    global heatingValveOpen
    heatingValveOpen = isOpen
    print(f"[DUMMY] heating valve -> {'OPEN' if isOpen else 'CLOSED'}")

# Dissolved oxygen & aerator cooler fan
def setDummyAeratorFan(active: bool):
    global aeratorFanActive, aeratorCoolerFanActive
    aeratorFanActive = active
    aeratorCoolerFanActive = active
    print(f"[DUMMY] aerator cooler fan -> {'ON' if active else 'OFF'}")

def setRpiAeratorFan(active: bool):
    global aeratorFanActive, aeratorCoolerFanActive
    aeratorFanActive = active
    aeratorCoolerFanActive = active
    if isRpiPresent and GPIO is not None:
        print(f"[RPI] aerator cooler fan -> {'ON' if active else 'OFF'}")
        GPIO.output(aeratorFanPin, GPIO.LOW if active else GPIO.HIGH)
    else:
        print(f"[DUMMY] aerator cooler fan -> {'ON' if active else 'OFF'}")

setAeratorFan = setRpiAeratorFan
setAeratorCoolerFan = setRpiAeratorFan

def _update_aerator_fan_state():
    """
    Ensure the aerator cooler fan is enabled whenever at least one
    aerator solenoid valve is turned on (_dosvcounter > 0), and disabled otherwise.
    """
    fan_needed = _dosvcounter > 0
    setAeratorFan(fan_needed)

def setDummyDoSolenoidValve(isOpen: bool):
    global _dosvcounter, doSolenoidValveOpen
    if isOpen:
        _dosvcounter += 1
    else:
        _dosvcounter -= 1
    _dosvcounter = max(0, min(3, _dosvcounter))
    doSolenoidValveOpen = (_dosvcounter > 0)
    print(f"[DUMMY] DO solenoid valves open: {_dosvcounter}/3")
    _update_aerator_fan_state()

def setRpiDoSolenoidValve(isOpen: bool):
    global doSolenoidValveOpen, _dosvcounter
    if isOpen is True:
        _dosvcounter += 1
    else:
        _dosvcounter -= 1
    if _dosvcounter > 3:
        print(f"[RPI] MAX valves OPEN -> {_dosvcounter}")
        _dosvcounter = 3
    elif _dosvcounter < 0:
        print(f"[RPI] ALL valves CLOSED -> {_dosvcounter}")
        _dosvcounter = 0

    doSolenoidValveOpen = (_dosvcounter > 0)

    if isRpiPresent and GPIO is not None:
        print(f"[RPI] DO solenoid valve -> {isOpen}")
        print(f"[RPI] DO solenoid valves open: {_dosvcounter}/3")
        if _dosvcounter == 3:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.LOW)
            GPIO.output(doSolenoidValve3, GPIO.LOW)
        elif _dosvcounter == 2:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.LOW)
            GPIO.output(doSolenoidValve3, GPIO.HIGH)
        elif _dosvcounter == 1:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
            GPIO.output(doSolenoidValve3, GPIO.HIGH)
        elif _dosvcounter == 0:
            GPIO.output(doSolenoidValve1, GPIO.HIGH)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
            GPIO.output(doSolenoidValve3, GPIO.HIGH)
    else:
        print(f"[DUMMY] DO solenoid valves open: {_dosvcounter}/3")

    _update_aerator_fan_state()

setDoSolenoidValve = setRpiDoSolenoidValve
doSolenoidValve = setDoSolenoidValve

def setDoSolenoidValveCount(count: int):
    """
    Directly set the number of open aerator solenoid valves (0 to 3).
    Automatically updates the cooler fan state.
    """
    global _dosvcounter, doSolenoidValveOpen
    count = max(0, min(3, int(count)))
    _dosvcounter = count
    doSolenoidValveOpen = (_dosvcounter > 0)
    if isRpiPresent and GPIO is not None:
        if _dosvcounter == 3:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.LOW)
            GPIO.output(doSolenoidValve3, GPIO.LOW)
        elif _dosvcounter == 2:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.LOW)
            GPIO.output(doSolenoidValve3, GPIO.HIGH)
        elif _dosvcounter == 1:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
            GPIO.output(doSolenoidValve3, GPIO.HIGH)
        elif _dosvcounter == 0:
            GPIO.output(doSolenoidValve1, GPIO.HIGH)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
            GPIO.output(doSolenoidValve3, GPIO.HIGH)
    else:
        print(f"[DUMMY] DO solenoid valves open: {_dosvcounter}/3")
    _update_aerator_fan_state()

def setDummyDoBleedValve(percent: float):
    global doBleedValvePercent
    doBleedValvePercent = max(0.0, min(100.0, percent))
    print(f"[DUMMY] DO bleed valve (stepper) -> {doBleedValvePercent:.1f}%")


def enableBleedValveMotor():
    """Enable the stepper motor driver (LOW = enabled)."""
    global bleedValveDriverEnabled
    bleedValveDriverEnabled = True
    if isRpiPresent and GPIO is not None:
        try:
            GPIO.output(bleedValveEnablePin, GPIO.LOW)
            print("[RPI] Bleed valve motor driver ENABLED (pin LOW)")
        except Exception as e:
            logging.error(f"[RPI] Failed to enable bleed valve motor driver: {e}")
    else:
        print("[DUMMY] Bleed valve motor driver ENABLED")


def disableBleedValveMotor():
    """Disable the stepper motor driver (HIGH = disabled)."""
    global bleedValveDriverEnabled
    bleedValveDriverEnabled = False
    if isRpiPresent and GPIO is not None:
        try:
            GPIO.output(bleedValveEnablePin, GPIO.HIGH)
            print("[RPI] Bleed valve motor driver DISABLED (pin HIGH)")
        except Exception as e:
            logging.error(f"[RPI] Failed to disable bleed valve motor driver: {e}")
    else:
        print("[DUMMY] Bleed valve motor driver DISABLED")


def initializeLed(app=None):
    """
    Initialize LED on startup. Reads stored led_intensity from DB
    and applies it using set_lux.
    """
    def _do_init():
        try:
            from app.models import get_control_settings
            settings = get_control_settings()
            target_lux = settings.get("led_intensity", 1000.0)
            set_lux(target_lux)
            prefix = "[RPI]" if isRpiPresent else "[DUMMY]"
            print(f"{prefix} LED restored to {target_lux} lux")
        except Exception as e:
            logging.warning(f"Failed to initialize LED setting from DB: {e}")

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


def initializeBleedValveMotor(app=None):
    """
    Initialize bleed valve stepper motor on startup. Reads stored motor position from DB
    and restores that as the current valve position without adjusting physical motor movement.
    Sets motorInit = True.
    """
    global motorInit, doBleedValvePercent

    def _do_init():
        global motorInit, doBleedValvePercent
        from app.models import load_motor_state

        enableBleedValveMotor()

        saved_pos = load_motor_state("bleed_valve")
        if saved_pos is None or not isinstance(saved_pos, int):
            saved_pos = 0

        saved_pos = max(0, min(len(BLEED_VALVE_POSITIONS) - 1, saved_pos))
        doBleedValvePercent = BLEED_VALVE_POSITIONS[saved_pos]

        motorInit = True
        prefix = "[RPI]" if isRpiPresent else "[DUMMY]"
        print(f"{prefix} Bleed valve motor restored to position {saved_pos} ({doBleedValvePercent:.1f}%) without physical movement")

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
        GPIO.output(bleedValveEnablePin, GPIO.LOW)
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
mainValve = setDummyMainValve
mainValveState = setDummyMainValve

coolingSystem = setDummyCoolingSystem
coolingValve = setDummyCoolingValve
heatingSystem = setDummyHeatingSystem
heatingValve = setDummyHeatingValve
mixer = setDummyMixer
mixingFan = setDummyMixer
setMixingFan = setDummyMixer
aeratorFan = setDummyAeratorFan
aeratorCoolerFan = setDummyAeratorFan

doSolenoidValve = setDummyDoSolenoidValve
doBleedValve = setDummyDoBleedValve

# RPi implementations (GPIO mode)
if isRpiPresent:
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(sourceValvePin, GPIO.OUT, initial=GPIO.HIGH)
    #GPIO.setup(drainValvePin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(mainValvePin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(pel1Pin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(pel2Pin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(pel3Pin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(pel4Pin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(fan1Pin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(fan2Pin, GPIO.OUT, initial=GPIO.HIGH)
    #GPIO.setup(fan3Pin, GPIO.OUT)
    #GPIO.setup(fan4Pin, GPIO.OUT)
    GPIO.setup(coolingValvePin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(heatingSystemPin, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(heatingValvePin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(mixerPin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(doSolenoidValve1, GPIO.OUT, initial=GPIO.LOW if _dosvcounter >= 1 else GPIO.HIGH)
    GPIO.setup(doSolenoidValve2, GPIO.OUT, initial=GPIO.LOW if _dosvcounter >= 2 else GPIO.HIGH)
    GPIO.setup(doSolenoidValve3, GPIO.OUT, initial=GPIO.LOW if _dosvcounter >= 3 else GPIO.HIGH)
    GPIO.setup(aeratorFanPin, GPIO.OUT, initial=GPIO.LOW if _dosvcounter > 0 else GPIO.HIGH)
    # Initialize LED PWM via pigpio DMA PWM
    if isPigpioPresent and pigpio is not None:
        try:
            temp_pi = pigpio.pi()
            if getattr(temp_pi, "connected", False):
                pigpio_pi = temp_pi
                pigpio_pi.set_mode(ledPWMPin, pigpio.OUTPUT)
                pigpio_pi.set_PWM_frequency(ledPWMPin, LED_PWM_FREQ)
                pigpio_pi.set_PWM_range(ledPWMPin, 255)
                pigpio_pi.set_PWM_dutycycle(ledPWMPin, 0)
                ledPwm = pigpio_pi
                mosfet = pigpio_pi
                print(f"[RPI] pigpio DMA PWM initialized on PIN {ledPWMPin} ({LED_PWM_FREQ}Hz)")
            else:
                logging.warning("[RPI] pigpio daemon not running. Run 'sudo pigpiod -t 1' for flicker-free LED.")
        except Exception as e:
            logging.error(f"[RPI] Failed to initialize pigpio for LED: {e}")

    # Fallback to RPi.GPIO software PWM if pigpio is not active
    if pigpio_pi is None:
        GPIO.setup(ledPWMPin, GPIO.OUT)
        try:
            ledPwm = GPIO.PWM(ledPWMPin, LED_PWM_FREQ)
            ledPwm.start(0)  # Start at 0% brightness
            mosfet = ledPwm
        except Exception as e:
            ledPwm = None
            mosfet = None
            logging.error(f"[RPI] Failed to initialize fallback LED PWM: {e}")

    # Stepper motor (Wantai 42bygh610-1) setup for bleed valve
    GPIO.setup(bleedValveDirPin, GPIO.OUT)
    GPIO.setup(bleedValveStepPin, GPIO.OUT)
    GPIO.setup(bleedValveEnablePin, GPIO.OUT)
    GPIO.output(bleedValveEnablePin, GPIO.LOW)  # Enable driver (LOW = enabled)
    bleedValveDriverEnabled = True

    setSourceValveState = setRpiSourceValveState
    setDrainValveState = setRpiDrainValveState

    setMainValve = setRpiMainValve
    setMixer = setRpiMixer
    _set_heater_relay = _setRpiHeaterRelay

    setCoolingSystem = setRpiCoolingSystem
    setCoolingValve = setRpiCoolingValve

    def setHeatingSystem(active: bool):
        global heatingSystemActive, _heating_thread
        with _heating_lock:
            heatingSystemActive = active
            print(f"[RPI] heating system -> {'ON' if active else 'OFF'}")
            if active:
                _update_mixer_state()
                _update_main_valve_state()
                if _heating_thread is None or not _heating_thread.is_alive():
                    _heating_stop_event.clear()
                    _heating_thread = threading.Thread(target=_heating_worker, daemon=True)
                    _heating_thread.start()
            else:
                _heating_stop_event.set()
                _set_heater_relay(False)
                _heating_thread = None
                _update_mixer_state()
                _update_main_valve_state()

    setHeatingValve = setRpiHeatingValve
    setAeratorFan = setRpiAeratorFan
    setAeratorCoolerFan = setRpiAeratorFan
    setDoSolenoidValve = setRpiDoSolenoidValve

    # override with RPi implementations
    sourceValveState = setSourceValveState
    drainValveState = setDrainValveState
    mainValve = setMainValve
    mainValveState = setMainValve
    coolingSystem = setCoolingSystem
    coolingValve = setCoolingValve
    heatingSystem = setHeatingSystem
    heatingValve = setHeatingValve
    mixer = setMixer
    mixingFan = setMixer
    setMixingFan = setMixer
    aeratorFan = setAeratorFan
    aeratorCoolerFan = setAeratorFan
    doSolenoidValve = setDoSolenoidValve
    doBleedValve = setDoBleedValveMotor


# -----------------------Control logic-----------------------
# Default tolerances:
#   Temperature      : +-0.5 °C
#   Dissolved Oxygen : +-0.3 mg/L

TEMP_TOLERANCE = 0.5   # degC either side of setpoint
DO_TOLERANCE   = 0.3   # mg/L either side of setpoint
DO_TRIG_INTERVAL = 30.0
_last_do_trig_time = 0.0

TEMP_CONSECUTIVE_REQUIRED = 3
_temp_consecutive_high = 0
_temp_consecutive_low = 0

MIXING_TANK_TEMP_LIMIT = 2.0  # Limit (C) above setpoint for heating or below setpoint for cooling
_heating_disabled_by_mixing = False
_cooling_disabled_by_mixing = False
_mix_consecutive_heat_disable = 0
_mix_consecutive_heat_enable = 0
_mix_consecutive_cool_disable = 0
_mix_consecutive_cool_enable = 0


def reset_temperature_control_state():
    """Reset consecutive reading counters for temperature threshold debouncing and mixing tank control."""
    global _temp_consecutive_high, _temp_consecutive_low
    global _heating_disabled_by_mixing, _cooling_disabled_by_mixing
    global _mix_consecutive_heat_disable, _mix_consecutive_heat_enable
    global _mix_consecutive_cool_disable, _mix_consecutive_cool_enable
    _temp_consecutive_high = 0
    _temp_consecutive_low = 0
    _heating_disabled_by_mixing = False
    _cooling_disabled_by_mixing = False
    _mix_consecutive_heat_disable = 0
    _mix_consecutive_heat_enable = 0
    _mix_consecutive_cool_disable = 0
    _mix_consecutive_cool_enable = 0


def applyTemperatureControl(
    temperature: float,
    setpoint: float,
    tolerance: float = TEMP_TOLERANCE,
    secondary_temp: float = None,
    consecutive_required: int = TEMP_CONSECUTIVE_REQUIRED,
    mixing_temp_limit: float = MIXING_TANK_TEMP_LIMIT,
):
    """
    Maintains water temperature and guarantees continuous water circulation:
    - Main valve: ALWAYS ON for constant circulation.
    - temp > setpoint + tolerance:
        Waits until 3 consecutive readings show that temperature has exceeded threshold
        before triggering cooling system.
        Once triggered (or while cooling mode is active):
        Cooling valve ON (flow from cooling), heating & heating valve OFF.
        Records last active system as 'cooling'.
        Mixing tank control:
        If mixing tank <= setpoint - mixing_temp_limit for 3 continuous readings,
        cooling system is disabled while cooling valve remains OPEN.
        Cooling system turns back ON only after 3 continuous readings above this limit.
    - temp < setpoint - tolerance:
        Waits until 3 consecutive readings show that temperature has dropped below threshold
        before triggering heating system.
        Once triggered (or while heating mode is active):
        Heating valve ON (flow from heat), cooling & cooling valve OFF.
        Records last active system as 'heating'.
        Mixing tank control:
        If mixing tank >= setpoint + mixing_temp_limit for 3 continuous readings,
        heating system is disabled while heating valve remains OPEN.
        Heating system turns back ON only after 3 continuous readings below this limit.
    - temp == setpoint (within tolerance band):
        Both heating and cooling active systems turn OFF.
        Consecutive high/low counters and mixing tank control states reset to 0/False.
        At least one valve remains OPEN to maintain constant circulation:
        - If last active was heating: heating valve OFF, cooling valve ON (flow from cooling).
        - If last active was cooling: cooling valve OFF, heating valve ON (flow from heating).
    """
    global last_temp_system, _temp_consecutive_high, _temp_consecutive_low
    global _heating_disabled_by_mixing, _cooling_disabled_by_mixing
    global _mix_consecutive_heat_disable, _mix_consecutive_heat_enable
    global _mix_consecutive_cool_disable, _mix_consecutive_cool_enable
    high = setpoint + tolerance
    low  = setpoint - tolerance

    # Resolve secondary temperature if provided
    if secondary_temp is None:
        secondary_temp = read_secondary_temp_sensor()
    if secondary_temp is None:
        secondary_temp = round(temperature + random.uniform(-0.5, 0.5), 2)

    # Ensure main valve is always open for constant circulation
    if not mainValveOpen:
        mainValve(True)

    if temperature > high:
        _temp_consecutive_high += 1
        _temp_consecutive_low = 0

        # If heating system was active or heating valve open, ensure it is immediately shut off
        if heatingSystemActive or heatingValveOpen:
            heatingSystem(False)
            heatingValve(False)
            _heating_disabled_by_mixing = False
            _mix_consecutive_heat_disable = 0
            _mix_consecutive_heat_enable = 0

        if _temp_consecutive_high >= consecutive_required or coolingSystemActive or _cooling_disabled_by_mixing:
            last_temp_system = "cooling"
            heatingSystem(False)
            heatingValve(False)
            _heating_disabled_by_mixing = False
            _mix_consecutive_heat_disable = 0
            _mix_consecutive_heat_enable = 0
            coolingValve(True)
            mainValve(True)

            mix_cool_limit = setpoint - mixing_temp_limit

            if not _cooling_disabled_by_mixing:
                if secondary_temp <= mix_cool_limit:
                    _mix_consecutive_cool_disable += 1
                    _mix_consecutive_cool_enable = 0
                    if _mix_consecutive_cool_disable >= consecutive_required:
                        print(f"[CONTROL] Mixing tank temp LOW ({secondary_temp}C <= {mix_cool_limit}C) [reading {_mix_consecutive_cool_disable}/{consecutive_required}] - disabling cooling system (cooling valve remains OPEN, mixing fan remains ON)")
                        _cooling_disabled_by_mixing = True
                        coolingSystem(False)
                        _mix_consecutive_cool_disable = 0
                    else:
                        print(f"[CONTROL] Mixing tank temp LOW ({secondary_temp}C <= {mix_cool_limit}C) [reading {_mix_consecutive_cool_disable}/{consecutive_required}] - waiting for {consecutive_required} consecutive readings before disabling cooling")
                        coolingSystem(True)
                else:
                    _mix_consecutive_cool_disable = 0
                    coolingSystem(True)
                    print(f"[CONTROL] Tank temp HIGH ({temperature}C > {high}C) [reading {_temp_consecutive_high}/{consecutive_required}] - cooling active (cooling valve ON, mixing tank {secondary_temp}C > {mix_cool_limit}C)")
            else:
                if secondary_temp > mix_cool_limit:
                    _mix_consecutive_cool_enable += 1
                    _mix_consecutive_cool_disable = 0
                    if _mix_consecutive_cool_enable >= consecutive_required:
                        print(f"[CONTROL] Mixing tank temp ABOVE limit ({secondary_temp}C > {mix_cool_limit}C) [reading {_mix_consecutive_cool_enable}/{consecutive_required}] - re-enabling cooling system")
                        _cooling_disabled_by_mixing = False
                        coolingSystem(True)
                        _mix_consecutive_cool_enable = 0
                    else:
                        print(f"[CONTROL] Mixing tank temp ABOVE limit ({secondary_temp}C > {mix_cool_limit}C) [reading {_mix_consecutive_cool_enable}/{consecutive_required}] - waiting for {consecutive_required} consecutive readings before re-enabling cooling")
                        coolingSystem(False)
                else:
                    _mix_consecutive_cool_enable = 0
                    coolingSystem(False)
                    print(f"[CONTROL] Cooling paused by mixing tank under-temp ({secondary_temp}C <= {mix_cool_limit}C) - cooling valve remains OPEN, mixing fan remains ON")
        else:
            print(f"[CONTROL] Tank temp HIGH ({temperature}C > {high}C) [reading {_temp_consecutive_high}/{consecutive_required}] - waiting for {consecutive_required} consecutive readings before triggering cooling")
            mainValve(True)
            if last_temp_system == "heating":
                heatingValve(False)
                coolingValve(True)
            else:
                coolingValve(False)
                heatingValve(True)

    elif temperature < low:
        _temp_consecutive_low += 1
        _temp_consecutive_high = 0

        # If cooling system was active or cooling valve open, ensure it is immediately shut off
        if coolingSystemActive or coolingValveOpen:
            coolingSystem(False)
            coolingValve(False)
            _cooling_disabled_by_mixing = False
            _mix_consecutive_cool_disable = 0
            _mix_consecutive_cool_enable = 0

        if _temp_consecutive_low >= consecutive_required or heatingSystemActive or _heating_disabled_by_mixing:
            last_temp_system = "heating"
            coolingSystem(False)
            coolingValve(False)
            _cooling_disabled_by_mixing = False
            _mix_consecutive_cool_disable = 0
            _mix_consecutive_cool_enable = 0
            heatingValve(True)
            mainValve(True)

            mix_heat_limit = setpoint + mixing_temp_limit

            if not _heating_disabled_by_mixing:
                if secondary_temp >= mix_heat_limit:
                    _mix_consecutive_heat_disable += 1
                    _mix_consecutive_heat_enable = 0
                    if _mix_consecutive_heat_disable >= consecutive_required:
                        print(f"[CONTROL] Mixing tank temp HIGH ({secondary_temp}C >= {mix_heat_limit}C) [reading {_mix_consecutive_heat_disable}/{consecutive_required}] - disabling heating system (heating valve remains OPEN, mixing fan remains ON)")
                        _heating_disabled_by_mixing = True
                        heatingSystem(False)
                        _mix_consecutive_heat_disable = 0
                    else:
                        print(f"[CONTROL] Mixing tank temp HIGH ({secondary_temp}C >= {mix_heat_limit}C) [reading {_mix_consecutive_heat_disable}/{consecutive_required}] - waiting for {consecutive_required} consecutive readings before disabling heating")
                        heatingSystem(True)
                else:
                    _mix_consecutive_heat_disable = 0
                    heatingSystem(True)
                    print(f"[CONTROL] Tank temp LOW ({temperature}C < {low}C) [reading {_temp_consecutive_low}/{consecutive_required}] - activating heating (heating valve ON, mixing tank {secondary_temp}C < {mix_heat_limit}C)")
            else:
                if secondary_temp < mix_heat_limit:
                    _mix_consecutive_heat_enable += 1
                    _mix_consecutive_heat_disable = 0
                    if _mix_consecutive_heat_enable >= consecutive_required:
                        print(f"[CONTROL] Mixing tank temp BELOW limit ({secondary_temp}C < {mix_heat_limit}C) [reading {_mix_consecutive_heat_enable}/{consecutive_required}] - re-enabling heating system")
                        _heating_disabled_by_mixing = False
                        heatingSystem(True)
                        _mix_consecutive_heat_enable = 0
                    else:
                        print(f"[CONTROL] Mixing tank temp BELOW limit ({secondary_temp}C < {mix_heat_limit}C) [reading {_mix_consecutive_heat_enable}/{consecutive_required}] - waiting for {consecutive_required} consecutive readings before re-enabling heating")
                        heatingSystem(False)
                else:
                    _mix_consecutive_heat_enable = 0
                    heatingSystem(False)
                    print(f"[CONTROL] Heating paused by mixing tank over-temp ({secondary_temp}C >= {mix_heat_limit}C) - heating valve remains OPEN, mixing fan remains ON")
        else:
            print(f"[CONTROL] Tank temp LOW ({temperature}C < {low}C) [reading {_temp_consecutive_low}/{consecutive_required}] - waiting for {consecutive_required} consecutive readings before triggering heating")
            mainValve(True)
            if last_temp_system == "heating":
                heatingValve(False)
                coolingValve(True)
            else:
                coolingValve(False)
                heatingValve(True)

    else:
        _temp_consecutive_high = 0
        _temp_consecutive_low = 0
        _heating_disabled_by_mixing = False
        _cooling_disabled_by_mixing = False
        _mix_consecutive_heat_disable = 0
        _mix_consecutive_heat_enable = 0
        _mix_consecutive_cool_disable = 0
        _mix_consecutive_cool_enable = 0
        print(f"[CONTROL] Main Tank temp STABLE ({temperature}C within +-{tolerance}C of setpoint {setpoint}C)")
        coolingSystem(False)
        heatingSystem(False)
        mainValve(True)

        if last_temp_system == "heating":
            heatingValve(False)
            coolingValve(True)
            print(f"[CONTROL] Circulation: Last on was heating -> heating valve OFF, cooling valve ON (flow from cooling)")
        else:
            coolingValve(False)
            heatingValve(True)
            print(f"[CONTROL] Circulation: Last on was cooling -> cooling valve OFF, heating valve ON (flow from heating)")


def applyDissolvedOxygenControl(do_level: float, setpoint: float, tolerance: float = DO_TOLERANCE):
    """
    Maintains dissolved oxygen level:
    - do_level <= setpoint - tolerance (deviation >= 0.3 mg/L below setpoint):
        DO control enabled -> opens aerator solenoid valve (increments open valve count),
        closes bleed valve by 33%.
    - setpoint - tolerance < do_level < setpoint:
        DO recovering -> maintains current number of open valves.
    - setpoint <= do_level <= setpoint + tolerance (between setpoint and 0.3 mg/L deviation above setpoint):
        DO at/above setpoint -> closes ALL DO aerator valves instead of maintaining current open valves,
        and fully opens bleed valve (100.0%) in response to all solenoid valves closing.
    - do_level > setpoint + tolerance (deviation > 0.3 mg/L above setpoint):
        DO HIGH -> all aerator valves closed, bleed valve fully opened (100.0%) to bleed excess oxygen.
    """
    global _last_do_trig_time
    high = round(setpoint + tolerance, 4)
    low  = round(setpoint - tolerance, 4)

    # Upper band: between setpoint and 0.3 mg/L deviation above setpoint
    if setpoint <= do_level <= high:
        print(f"[CONTROL] DO within setpoint to +{tolerance} mg/L ({do_level} mg/L in [{setpoint}, {high}] mg/L) - CLOSING ALL AERATOR VALVES & FULLY OPENING BLEED VALVE")
        if _dosvcounter > 0:
            setDoSolenoidValveCount(0)
        doBleedValve(100.0)
        _last_do_trig_time = 0.0  # Reset trigger timer so re-enabling can respond promptly if needed
        return

    # High DO: exceeding setpoint + tolerance
    if do_level > high:
        if _dosvcounter > 0:
            print(f"[CONTROL] DO HIGH ({do_level} mg/L > {high} mg/L) - closing all aerator valves & fully opening bleed valve")
            setDoSolenoidValveCount(0)
        doBleedValve(100.0)
        return

    # Stable / recovering: between (setpoint - tolerance) and setpoint
    if low < do_level < setpoint:
        print(f"[CONTROL] DO RECOVERING ({do_level} mg/L within -{tolerance} mg/L of setpoint {setpoint} mg/L) - maintaining {_dosvcounter}/3 open valves, bleed valve {doBleedValvePercent:.1f}%")
        return

    # Low DO: deviation has reached or exceeded 0.3 mg/L below setpoint
    now = time.monotonic()
    if now - _last_do_trig_time < DO_TRIG_INTERVAL:
        return
    _last_do_trig_time = now

    print(f"[CONTROL] DO LOW ({do_level} mg/L <= {low} mg/L, setpoint {setpoint} mg/L) - enabling DO control: opening aerator solenoid, closing bleed 33%")
    doSolenoidValve(True)
    doBleedValve(max(0.0, doBleedValvePercent - 33.0))


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
    """
    Shared DS18B20 read-and-parse logic.
    Handles CRC checks and filters out the 85.0°C power-on reset artifact (unconverted scratchpad value)
    by waiting for conversion (0.8s) and retrying up to 5 times. Rejects persistent 85.0°C readings.
    """
    import time as _time
    for attempt in range(5):
        try:
            with open(device_file, "r") as f:
                lines = f.readlines()
        except Exception:
            _time.sleep(0.2)
            continue

        retries = 0
        while (not lines or len(lines) < 2 or lines[0].strip()[-3:] != "YES") and retries < 5:
            _time.sleep(0.2)
            try:
                with open(device_file, "r") as f:
                    lines = f.readlines()
            except Exception:
                pass
            retries += 1

        if not lines or len(lines) < 2 or lines[0].strip()[-3:] != "YES":
            _time.sleep(0.2)
            continue

        equals_pos = lines[1].find("t=")
        if equals_pos == -1:
            print(f"[RPI] {label} DS18B20 parse error - using simulated value")
            return None

        try:
            temp_c = float(lines[1][equals_pos + 2:].strip()) / 1000.0
        except (ValueError, IndexError):
            _time.sleep(0.2)
            continue

        # Check for DS18B20 power-on reset value (85.0°C)
        if abs(temp_c - 85.0) < 0.01:
            print(f"[RPI] {label} DS18B20 returned 85.0C (power-on reset value), waiting for conversion (attempt {attempt + 1}/5)...")
            _time.sleep(0.8)
            continue

        print(f"[RPI] {label} DS18B20 reading: {temp_c:.2f}C")
        return round(temp_c, 2)

    print(f"[RPI] {label} DS18B20 returned persistent invalid 85.0C or failed read - ignoring invalid reading")
    return None


def read_temp_sensor():
    """
    Read temperature (C) from the DS18B20 1-Wire sensor.
    Returns a float on RPi when the sensor is present and valid,
    or None otherwise (then use simulated value). Rejects invalid 85.0C reading.
    """
    if not isRpiPresent or _temp_device_file is None:
        print("[DUMMY] Main Tank temperature sensor read - using simulated value")
        return None
    try:
        val = _parse_ds18b20(_temp_device_file, "primary")
        if val is not None and abs(val - 85.0) < 0.01:
            print("[RPI] Main Tank DS18B20 rejected invalid 85.0C reading")
            return None
        return val
    except Exception as e:
        print(f"[RPI] Main Tank DS18B20 read failed ({e}) - using simulated value")
        return None


def read_secondary_temp_sensor():
    """
    Read temperature (C) from the Mixing tank DS18B20 1-Wire sensor.
    Returns a float on RPi when the sensor is present and valid,
    or None otherwise (then use simulated value). Rejects invalid 85.0C reading.
    """
    if not isRpiPresent or _temp_secondary_device_file is None:
        print("[DUMMY] Mixing tank temperature sensor read - using simulated value")
        return None
    try:
        val = _parse_ds18b20(_temp_secondary_device_file, "secondary")
        if val is not None and abs(val - 85.0) < 0.01:
            print("[RPI] Mixing tank DS18B20 rejected invalid 85.0C reading")
            return None
        return val
    except Exception as e:
        print(f"[RPI] Mixing tank DS18B20 read failed ({e}) - using simulated value")
        return None


def cleanupGpio():
    """
    Disable stepper motor driver (ENABLE pin HIGH), turn off heater relay,
    cancel mixer timer, turn off mixer, and cleanup GPIO pins on shutdown.
    """
    global bleedValveDriverEnabled, _mixer_timer, heatingSystemActive, _heating_thread, pigpio_pi, ledPwm, mosfet
    if _mixer_timer is not None:
        try:
            _mixer_timer.cancel()
            _mixer_timer = None
        except Exception:
            pass

    with _heating_lock:
        heatingSystemActive = False
        _heating_stop_event.set()
        _set_heater_relay(False)
        _heating_thread = None

    coolingSystem(False)
    mixer(False)
    mainValve(False)
    setAeratorFan(False)

    if isRpiPresent and GPIO is not None:
        try:
            print("[RPI] Closing main water valve...")
            GPIO.output(mainValvePin, GPIO.HIGH)
            GPIO.output(sourceValvePin, GPIO.HIGH)
            GPIO.output(coolingValvePin, GPIO.HIGH)
            GPIO.output(heatingValvePin, GPIO.HIGH)
        except Exception as e:
            logging.warning(f"[RPI] Exception while resetting main valve: {e}")
        try:
            print("[RPI] Turning off peltier cooling elements and fans...")
            GPIO.output(pel1Pin, GPIO.HIGH)
            GPIO.output(pel2Pin, GPIO.HIGH)
            GPIO.output(pel3Pin, GPIO.HIGH)
            GPIO.output(pel4Pin, GPIO.HIGH)
            GPIO.output(fan1Pin, GPIO.HIGH)
            GPIO.output(fan2Pin, GPIO.HIGH)
        except Exception as e:
            logging.warning(f"[RPI] Exception while resetting cooling system: {e}")
        try:
            print("[RPI] Turning off aerator cooler fan...")
            GPIO.output(aeratorFanPin, GPIO.HIGH)
            print("[RPI] Closing aerator solenoid valves...")
            GPIO.output(doSolenoidValve1, GPIO.HIGH)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
            GPIO.output(doSolenoidValve3, GPIO.HIGH)
        except Exception as e:
            logging.warning(f"[RPI] Exception while resetting aerator fan and valves: {e}")
        try:
            print("[RPI] Turning off heater relay and mixer...")
            GPIO.output(heatingSystemPin, GPIO.LOW)
            GPIO.output(mixerPin, GPIO.HIGH)
        except Exception as e:
            logging.warning(f"[RPI] Exception while resetting heater/mixer: {e}")
        try:
            if pigpio_pi is not None:
                print("[RPI] Stopping pigpio LED PWM...")
                try:
                    pigpio_pi.set_PWM_dutycycle(ledPWMPin, 0)
                except Exception:
                    pass
                try:
                    pigpio_pi.stop()
                except Exception:
                    pass
                pigpio_pi = None
            if ledPwm is not None:
                print("[RPI] Stopping LED PWM...")
                try:
                    if hasattr(ledPwm, "ChangeDutyCycle"):
                        ledPwm.ChangeDutyCycle(0)
                except Exception:
                    pass
                try:
                    if hasattr(ledPwm, "stop"):
                        ledPwm.stop()
                except Exception:
                    pass
                ledPwm = None
                mosfet = None
        except Exception as e:
            logging.warning(f"[RPI] Exception while stopping LED PWM: {e}")
        try:
            print("[RPI] Disabling bleed valve motor driver (pin HIGH)...")
            GPIO.output(bleedValveEnablePin, GPIO.HIGH)
            bleedValveDriverEnabled = False
        except Exception as e:
            logging.warning(f"[RPI] Exception while disabling motor driver: {e}")
        try:
            print("[RPI] Cleaning up GPIO pins...")
            GPIO.cleanup()
        except Exception as e:
            logging.warning(f"[RPI] Exception while cleaning up GPIO: {e}")


atexit.register(cleanupGpio)
