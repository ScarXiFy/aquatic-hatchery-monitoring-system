import atexit
import random
import time
import logging
import threading

# Timing configuration for mixing and water pump control
MIXER_SHUTDOWN_DELAY = 10.0       # Run-on time in seconds for mixer after cooling/heating stops
HEATING_PUMP_START_DELAY = 40.0   # Seconds after heating valve opens before heating tank pump activates (TBD)
HEATING_PUMP_RUN_DURATION = 120.0 # Seconds (2 minutes) heating tank pump stays active after timed activation
COOLING_PUMP_START_DELAY = 40.0   # Seconds after cooling valve opens before cooling tank pump activates (TBD)
COOLING_PUMP_RUN_DURATION = 120.0 # Seconds (2 minutes) cooling tank pump stays active after timed activation

# Heating system relay & Mixer motor pins
heatingSystemPin = 26
mixerPin = 5

# Water pump relay control pins (Active-LOW: LOW=ON, HIGH=OFF)
heatingPumpPin = 11               # Water pump directed to the heating tank
coolingPumpPin = 0                # Water pump directed to the cooling tank

# Peltier cooling pins (8 cooling peltier modules)
pel1Pin = 12
pel2Pin = 16
pel3Pin = 20
pel4Pin = 21
pel5Pin = 14
pel6Pin = 15
pel7Pin = 7
pel8Pin = 1
PELTIER_PINS = [pel1Pin, pel2Pin, pel3Pin, pel4Pin, pel5Pin, pel6Pin, pel7Pin, pel8Pin]

# Stepper motor pins for Heating Valve (enable=6, dir=13, pul=19)
heatValveEnablePin = 6
heatValveDirPin = 13
heatValvePulPin = 19
heatingValveEnablePin = heatValveEnablePin
heatingValveDirPin = heatValveDirPin
heatingValvePulPin = heatValvePulPin

# Stepper motor pins for Cooling Valve (enable=25, dir=24, pul=23)
coolValveEnablePin = 25
coolValveDirPin = 24
coolValvePulPin = 23
coolingValveEnablePin = coolValveEnablePin
coolingValveDirPin = coolValveDirPin
coolingValvePulPin = coolValvePulPin

# Stepper valve parameters
VALVE_STEP_AMOUNT = 178
VALVE_STEPS = 178
VALVE_STEP_DELAY = 0.001

# Water source valve pin
sourceValvePin = 8

# Legacy valve pin aliases (main valve removed, heating/cooling use steppers)
mainValvePin = None
coolingValvePin = None
heatingValvePin = None

# Fans removed from GPIO except mixing (mixerPin = 5)
fan1Pin = None
fan2Pin = None
aeratorFanPin = None
aeratorCoolerFanPin = None

# LED Lighting PWM configuration (MOSFET driver)
ledPWMPin = 18
PWM_PIN = ledPWMPin
LED_PWM_FREQ = 1000          # 1000Hz frequency for flicker-free LED operation
MAX_LUX = 3000

# Dissolved oxygen control pins (2 solenoid valves: GPIO 10 and GPIO 9)
DO_SOLENOID_VALVE_COUNT = 2
doSolenoidValve1 = 10
doSolenoidValve2 = 9
doSolenoidValve3 = None           # Removed; GPIO 11 reassigned to heating tank water pump relay

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
coolingValveOpen = True   # Initial stored value: OPEN for cooling
heatingSystemActive = False
heatingValveOpen = False  # Initial stored value: CLOSED for heating
heaterActive = False
heatingPumpActive = False
coolingPumpActive = False
mixerActive = False
mixingFanActive = False
last_temp_system = "cooling"  # Tracks last active temperature system ("heating" or "cooling")
_heating_disabled_by_mixing = False
_cooling_disabled_by_mixing = False

# Heating tank sensor-based control state
HEATING_TANK_MIN_SETPOINT = 27.0
HEATING_TANK_MAX_SETPOINT = 40.0
heatingTankSetpoint = 40.0
HEATING_TANK_TEMP_TOLERANCE = 0.0
_heating_tank_setpoint_just_set = False
_heating_pump_cooldown_active = False

# Synchronization for mixer, heater, and water pumps
_mixer_timer = None
_mixer_lock = threading.Lock()

_heating_lock = threading.Lock()
_pump_lock = threading.RLock()
_heating_pump_start_timer = None
_heating_pump_stop_timer = None
_cooling_pump_start_timer = None
_cooling_pump_stop_timer = None

doSolenoidValveOpen = False
doBleedValvePercent = 0  # 0-100 %
bleedValveDriverEnabled = False
_dosvcounter = DO_SOLENOID_VALVE_COUNT
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

# Default main valve pointer (PC mode)
mainValve = setDummyMainValve
mainValveState = setDummyMainValve

def _update_main_valve_state():
    # Mixing tank to main tank valve removed entirely from hardware; track software state
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

def setDummyHeatingPump(active: bool):
    global heatingPumpActive
    heatingPumpActive = bool(active)
    print(f"[DUMMY] heating tank water pump (GPIO {heatingPumpPin}) -> {'ON' if heatingPumpActive else 'OFF'}")


def setDummyCoolingPump(active: bool):
    global coolingPumpActive
    coolingPumpActive = bool(active)
    print(f"[DUMMY] cooling tank water pump (GPIO {coolingPumpPin}) -> {'ON' if coolingPumpActive else 'OFF'}")


def setRpiHeatingPump(active: bool):
    global heatingPumpActive
    heatingPumpActive = bool(active)
    print(f"[RPI] heating tank water pump (GPIO {heatingPumpPin}) -> {'ON' if heatingPumpActive else 'OFF'}")
    if isRpiPresent and GPIO is not None:
        GPIO.output(heatingPumpPin, GPIO.LOW if heatingPumpActive else GPIO.HIGH)


def setRpiCoolingPump(active: bool):
    global coolingPumpActive
    coolingPumpActive = bool(active)
    print(f"[RPI] cooling tank water pump (GPIO {coolingPumpPin}) -> {'ON' if coolingPumpActive else 'OFF'}")
    if isRpiPresent and GPIO is not None:
        GPIO.output(coolingPumpPin, GPIO.LOW if coolingPumpActive else GPIO.HIGH)


heatingPump = setDummyHeatingPump
setHeatingPump = setDummyHeatingPump
coolingPump = setDummyCoolingPump
setCoolingPump = setDummyCoolingPump


def cancel_pump_timers():
    """Cancel any pending heating or cooling water pump start/stop timers."""
    global _heating_pump_start_timer, _heating_pump_stop_timer
    global _cooling_pump_start_timer, _cooling_pump_stop_timer
    with _pump_lock:
        for attr in (
            "_heating_pump_start_timer",
            "_heating_pump_stop_timer",
            "_cooling_pump_start_timer",
            "_cooling_pump_stop_timer",
        ):
            timer = globals().get(attr)
            if timer is not None:
                try:
                    timer.cancel()
                except Exception:
                    pass
                globals()[attr] = None


def _schedule_heating_pump_after_valve_open():
    """
    Schedules heating tank water pump activation HEATING_PUMP_START_DELAY seconds (40s)
    after the heating control valve opens, and deactivation HEATING_PUMP_RUN_DURATION
    seconds (2 minutes) after activation.
    """
    global _heating_pump_start_timer
    with _pump_lock:
        if _heating_pump_start_timer is not None:
            _heating_pump_start_timer.cancel()
            _heating_pump_start_timer = None

        def _activate():
            global _heating_pump_start_timer, _heating_pump_stop_timer
            with _pump_lock:
                _heating_pump_start_timer = None
                heatingPump(True)
                if _heating_pump_stop_timer is not None:
                    _heating_pump_stop_timer.cancel()

                def _deactivate():
                    global _heating_pump_stop_timer
                    with _pump_lock:
                        _heating_pump_stop_timer = None
                        if not _heating_pump_cooldown_active:
                            heatingPump(False)

                _heating_pump_stop_timer = threading.Timer(HEATING_PUMP_RUN_DURATION, _deactivate)
                _heating_pump_stop_timer.daemon = True
                _heating_pump_stop_timer.start()
                print(
                    f"[CONTROL] Heating tank water pump activated - will deactivate in {HEATING_PUMP_RUN_DURATION}s"
                )

        _heating_pump_start_timer = threading.Timer(HEATING_PUMP_START_DELAY, _activate)
        _heating_pump_start_timer.daemon = True
        _heating_pump_start_timer.start()
        print(
            f"[CONTROL] Heating valve opened - heating tank water pump will activate in {HEATING_PUMP_START_DELAY}s"
        )


def _schedule_cooling_pump_after_valve_open():
    """
    Schedules cooling tank water pump activation COOLING_PUMP_START_DELAY seconds (40s)
    after the cooling control valve opens, and deactivation COOLING_PUMP_RUN_DURATION
    seconds (2 minutes) after activation.
    """
    global _cooling_pump_start_timer
    with _pump_lock:
        if _cooling_pump_start_timer is not None:
            _cooling_pump_start_timer.cancel()
            _cooling_pump_start_timer = None

        def _activate():
            global _cooling_pump_start_timer, _cooling_pump_stop_timer
            with _pump_lock:
                _cooling_pump_start_timer = None
                coolingPump(True)
                if _cooling_pump_stop_timer is not None:
                    _cooling_pump_stop_timer.cancel()

                def _deactivate():
                    global _cooling_pump_stop_timer
                    with _pump_lock:
                        _cooling_pump_stop_timer = None
                        coolingPump(False)

                _cooling_pump_stop_timer = threading.Timer(COOLING_PUMP_RUN_DURATION, _deactivate)
                _cooling_pump_stop_timer.daemon = True
                _cooling_pump_stop_timer.start()
                print(
                    f"[CONTROL] Cooling tank water pump activated - will deactivate in {COOLING_PUMP_RUN_DURATION}s"
                )

        _cooling_pump_start_timer = threading.Timer(COOLING_PUMP_START_DELAY, _activate)
        _cooling_pump_start_timer.daemon = True
        _cooling_pump_start_timer.start()
        print(
            f"[CONTROL] Cooling valve opened - cooling tank water pump will activate in {COOLING_PUMP_START_DELAY}s"
        )

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

setDrainValve = setRpiDrainValveState

_valve_motor_lock = threading.Lock()

def _step_valve_motor(enable_pin: int, dir_pin: int, pul_pin: int, direction, steps: int = VALVE_STEP_AMOUNT):
    """
    Steps a valve stepper motor driver:
    - enable_pin: active LOW (MOTOR_ENABLE)
    - dir_pin: CW (close) or CCW (open)
    - pul_pin: step pulse pin
    """
    if not (isRpiPresent and GPIO is not None):
        return
    with _valve_motor_lock:
        try:
            GPIO.output(enable_pin, MOTOR_ENABLE)
            GPIO.output(dir_pin, direction)
            time.sleep(0.0001)
            for _ in range(steps):
                GPIO.output(pul_pin, GPIO.HIGH)
                time.sleep(VALVE_STEP_DELAY)
                GPIO.output(pul_pin, GPIO.LOW)
                time.sleep(VALVE_STEP_DELAY)
        except Exception as e:
            logging.error(f"[RPI] GPIO failure during valve motor movement on pin {pul_pin}: {e}")
            print(f"[RPI] GPIO failure during valve motor movement on pin {pul_pin}: {e}")

def setRpiCoolingValve(isOpen: bool):
    global coolingValveOpen
    if isOpen == coolingValveOpen:
        return
    direction = CCW if isOpen else CW
    action_str = "OPENING (CCW)" if isOpen else "CLOSING (CW)"
    print(f"[RPI] cooling valve stepper -> {action_str} {VALVE_STEP_AMOUNT} steps (state: {'OPEN' if isOpen else 'CLOSED'})")
    if isRpiPresent and GPIO is not None:
        _step_valve_motor(coolValveEnablePin, coolValveDirPin, coolValvePulPin, direction, VALVE_STEP_AMOUNT)
    coolingValveOpen = isOpen
    if isOpen:
        _schedule_cooling_pump_after_valve_open()
    try:
        from app.models import save_motor_state
        save_motor_state("cooling_valve", 1 if isOpen else 0)
    except Exception:
        pass

setCoolingValve = setRpiCoolingValve

def setRpiHeatingValve(isOpen: bool):
    global heatingValveOpen
    if isOpen == heatingValveOpen:
        return
    direction = CCW if isOpen else CW
    action_str = "OPENING (CCW)" if isOpen else "CLOSING (CW)"
    print(f"[RPI] heating valve stepper -> {action_str} {VALVE_STEP_AMOUNT} steps (state: {'OPEN' if isOpen else 'CLOSED'})")
    if isRpiPresent and GPIO is not None:
        _step_valve_motor(heatValveEnablePin, heatValveDirPin, heatValvePulPin, direction, VALVE_STEP_AMOUNT)
    heatingValveOpen = isOpen
    if isOpen:
        _schedule_heating_pump_after_valve_open()
    try:
        from app.models import save_motor_state
        save_motor_state("heating_valve", 1 if isOpen else 0)
    except Exception:
        pass

setHeatingValve = setRpiHeatingValve

# Temperature
def setDummyCoolingSystem(active: bool):
    global coolingSystemActive
    coolingSystemActive = active
    print(f"[DUMMY] cooling system (8 peltiers) -> {'ON' if active else 'OFF'}")
    _update_mixer_state()
    _update_main_valve_state()

def setRpiCoolingSystem(active: bool):
    global coolingSystemActive
    coolingSystemActive = active
    if isRpiPresent and GPIO is not None:
        print(f"[RPI] cooling system (8 peltiers) -> {'ON' if active else 'OFF'}")
        for pin in PELTIER_PINS:
            GPIO.output(pin, GPIO.LOW if active else GPIO.HIGH)
    else:
        print(f"[DUMMY] cooling system (8 peltiers) -> {'ON' if active else 'OFF'}")
    _update_mixer_state()
    _update_main_valve_state()

setCoolingSystem = setDummyCoolingSystem

def setDummyCoolingValve(isOpen: bool):
    global coolingValveOpen
    if isOpen == coolingValveOpen:
        return
    coolingValveOpen = isOpen
    if isOpen:
        _schedule_cooling_pump_after_valve_open()
    try:
        from app.models import save_motor_state
        save_motor_state("cooling_valve", 1 if isOpen else 0)
    except Exception:
        pass
    direction_str = "CCW" if isOpen else "CW"
    action_str = "OPEN" if isOpen else "CLOSED"
    print(f"[DUMMY] cooling valve stepper -> {action_str} ({direction_str}, {VALVE_STEP_AMOUNT} steps)")

def setDummyHeatingSystem(active: bool):
    global heatingSystemActive
    with _heating_lock:
        heatingSystemActive = active
        print(f"[DUMMY] heating system -> {'ON' if active else 'OFF'}")
        _update_mixer_state()
        _update_main_valve_state()

def setDummyHeatingValve(isOpen: bool):
    global heatingValveOpen
    if isOpen == heatingValveOpen:
        return
    heatingValveOpen = isOpen
    if isOpen:
        _schedule_heating_pump_after_valve_open()
    try:
        from app.models import save_motor_state
        save_motor_state("heating_valve", 1 if isOpen else 0)
    except Exception:
        pass
    direction_str = "CCW" if isOpen else "CW"
    action_str = "OPEN" if isOpen else "CLOSED"
    print(f"[DUMMY] heating valve stepper -> {action_str} ({direction_str}, {VALVE_STEP_AMOUNT} steps)")

# Dissolved oxygen & aerator cooler fan (software tracking only; fan removed from GPIO)
def setDummyAeratorFan(active: bool):
    global aeratorFanActive, aeratorCoolerFanActive
    aeratorFanActive = active
    aeratorCoolerFanActive = active

def setRpiAeratorFan(active: bool):
    global aeratorFanActive, aeratorCoolerFanActive
    aeratorFanActive = active
    aeratorCoolerFanActive = active

setAeratorFan = setRpiAeratorFan
setAeratorCoolerFan = setRpiAeratorFan

def _update_aerator_fan_state():
    """
    Ensure the aerator cooler fan state is tracked when at least one
    aerator solenoid valve is turned on (_dosvcounter > 0), and disabled otherwise.
    Note: Hardware fan has been removed from GPIO.
    """
    fan_needed = _dosvcounter > 0
    setAeratorFan(fan_needed)

def setDummyDoSolenoidValve(isOpen: bool):
    global _dosvcounter, doSolenoidValveOpen
    if isOpen:
        _dosvcounter += 1
    else:
        _dosvcounter -= 1
    _dosvcounter = max(0, min(DO_SOLENOID_VALVE_COUNT, _dosvcounter))
    doSolenoidValveOpen = (_dosvcounter > 0)
    print(f"[DUMMY] DO solenoid valves open: {_dosvcounter}/{DO_SOLENOID_VALVE_COUNT}")
    _update_aerator_fan_state()

def setRpiDoSolenoidValve(isOpen: bool):
    global doSolenoidValveOpen, _dosvcounter
    if isOpen is True:
        _dosvcounter += 1
    else:
        _dosvcounter -= 1
    if _dosvcounter > DO_SOLENOID_VALVE_COUNT:
        print(f"[RPI] MAX valves OPEN -> {_dosvcounter}")
        _dosvcounter = DO_SOLENOID_VALVE_COUNT
    elif _dosvcounter < 0:
        print(f"[RPI] ALL valves CLOSED -> {_dosvcounter}")
        _dosvcounter = 0

    doSolenoidValveOpen = (_dosvcounter > 0)

    if isRpiPresent and GPIO is not None:
        print(f"[RPI] DO solenoid valve -> {isOpen}")
        print(f"[RPI] DO solenoid valves open: {_dosvcounter}/{DO_SOLENOID_VALVE_COUNT}")
        if _dosvcounter == 2:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.LOW)
        elif _dosvcounter == 1:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
        elif _dosvcounter == 0:
            GPIO.output(doSolenoidValve1, GPIO.HIGH)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
    else:
        print(f"[DUMMY] DO solenoid valves open: {_dosvcounter}/{DO_SOLENOID_VALVE_COUNT}")

    _update_aerator_fan_state()

setDoSolenoidValve = setRpiDoSolenoidValve
doSolenoidValve = setDoSolenoidValve

def setDoSolenoidValveCount(count: int):
    """
    Directly set the number of open aerator solenoid valves (0 to 2).
    Automatically updates the cooler fan state.
    """
    global _dosvcounter, doSolenoidValveOpen
    count = max(0, min(DO_SOLENOID_VALVE_COUNT, int(count)))
    _dosvcounter = count
    doSolenoidValveOpen = (_dosvcounter > 0)
    if isRpiPresent and GPIO is not None:
        if _dosvcounter == 2:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.LOW)
        elif _dosvcounter == 1:
            GPIO.output(doSolenoidValve1, GPIO.LOW)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
        elif _dosvcounter == 0:
            GPIO.output(doSolenoidValve1, GPIO.HIGH)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
    else:
        print(f"[DUMMY] DO solenoid valves open: {_dosvcounter}/{DO_SOLENOID_VALVE_COUNT}")
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


def initializeValveMotorStates(app=None):
    """
    Initializes and restores motor opening status for heating and cooling valves from DB.
    Initial defaults stored:
      - Heating valve: Closed (0)
      - Cooling valve: Open (1)
    Ensures that even though the valve is initially set to a certain position, it is not
    assumed to be the opposite position. Movement is skipped if state already matches.
    """
    global coolingValveOpen, heatingValveOpen

    def _do_init():
        global coolingValveOpen, heatingValveOpen
        try:
            from app.models import load_motor_state, init_motor_state
            init_motor_state()
            saved_cool = load_motor_state("cooling_valve")
            if saved_cool is not None:
                coolingValveOpen = bool(saved_cool == 1)
            else:
                coolingValveOpen = True

            saved_heat = load_motor_state("heating_valve")
            if saved_heat is not None:
                heatingValveOpen = bool(saved_heat == 1)
            else:
                heatingValveOpen = False

            prefix = "[RPI]" if isRpiPresent else "[DUMMY]"
            print(f"{prefix} Valve motor opening status initialized: cooling={'OPEN' if coolingValveOpen else 'CLOSED'}, heating={'OPEN' if heatingValveOpen else 'CLOSED'}")
        except Exception as e:
            logging.warning(f"Could not restore valve motor states: {e}")

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

    # Source water valve (Active-LOW: LOW=open, HIGH=closed)
    GPIO.setup(sourceValvePin, GPIO.OUT, initial=GPIO.HIGH)

    # 8 Peltier cooling modules (Active-LOW: LOW=ON, HIGH=OFF)
    for pin in PELTIER_PINS:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)

    # Heating system (heater relay)
    GPIO.setup(heatingSystemPin, GPIO.OUT, initial=GPIO.LOW)

    # Mixer motor (Active-LOW: LOW=ON, HIGH=OFF)
    GPIO.setup(mixerPin, GPIO.OUT, initial=GPIO.HIGH)

    # Water pump relays for heating tank (GPIO 11) and cooling tank (GPIO 0) (Active-LOW: LOW=ON, HIGH=OFF)
    GPIO.setup(heatingPumpPin, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(coolingPumpPin, GPIO.OUT, initial=GPIO.HIGH)

    # Dissolved oxygen aerator solenoid valves (2 valves: GPIO 10, GPIO 9)
    GPIO.setup(doSolenoidValve1, GPIO.OUT, initial=GPIO.LOW if _dosvcounter >= 1 else GPIO.HIGH)
    GPIO.setup(doSolenoidValve2, GPIO.OUT, initial=GPIO.LOW if _dosvcounter >= 2 else GPIO.HIGH)

    # Stepper motor pins for Heating Valve (enable=6, dir=13, pul=19)
    GPIO.setup(heatValveEnablePin, GPIO.OUT)
    GPIO.setup(heatValveDirPin, GPIO.OUT)
    GPIO.setup(heatValvePulPin, GPIO.OUT)
    GPIO.output(heatValveEnablePin, MOTOR_ENABLE)

    # Stepper motor pins for Cooling Valve (enable=25, dir=24, pul=23)
    GPIO.setup(coolValveEnablePin, GPIO.OUT)
    GPIO.setup(coolValveDirPin, GPIO.OUT)
    GPIO.setup(coolValvePulPin, GPIO.OUT)
    GPIO.output(coolValveEnablePin, MOTOR_ENABLE)
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
        global heatingSystemActive
        with _heating_lock:
            heatingSystemActive = active
            print(f"[RPI] heating system -> {'ON' if active else 'OFF'}")
            _update_mixer_state()
            _update_main_valve_state()

    setHeatingValve = setRpiHeatingValve
    setHeatingPump = setRpiHeatingPump
    setCoolingPump = setRpiCoolingPump
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
    heatingPump = setHeatingPump
    coolingPump = setCoolingPump
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
_heating_tank_consecutive_low = 0
_heating_tank_consecutive_high = 0

MIXING_TANK_TEMP_LIMIT = 1.0  # Limit (C) above setpoint for heating or below setpoint for cooling
_heating_disabled_by_mixing = False
_cooling_disabled_by_mixing = False
_mix_consecutive_heat_disable = 0
_mix_consecutive_heat_enable = 0
_mix_consecutive_cool_disable = 0
_mix_consecutive_cool_enable = 0


def reset_temperature_control_state():
    """Reset consecutive reading counters for temperature threshold debouncing, mixing tank control, and heating tank pump state."""
    global _temp_consecutive_high, _temp_consecutive_low
    global _heating_tank_consecutive_low, _heating_tank_consecutive_high
    global _heating_disabled_by_mixing, _cooling_disabled_by_mixing
    global _mix_consecutive_heat_disable, _mix_consecutive_heat_enable
    global _mix_consecutive_cool_disable, _mix_consecutive_cool_enable
    global _heating_tank_setpoint_just_set, _heating_pump_cooldown_active
    _temp_consecutive_high = 0
    _temp_consecutive_low = 0
    _heating_tank_consecutive_low = 0
    _heating_tank_consecutive_high = 0
    _heating_disabled_by_mixing = False
    _cooling_disabled_by_mixing = False
    _mix_consecutive_heat_disable = 0
    _mix_consecutive_heat_enable = 0
    _mix_consecutive_cool_disable = 0
    _mix_consecutive_cool_enable = 0
    _heating_tank_setpoint_just_set = False
    _heating_pump_cooldown_active = False
    cancel_pump_timers()


def set_heating_tank_setpoint(setpoint: float):
    """
    Record a newly configured heating tank temperature setpoint from the web app slider.
    Clamps within [HEATING_TANK_MIN_SETPOINT (27C), HEATING_TANK_MAX_SETPOINT (40C)] and
    marks the setpoint as just set so that if the heating tank sensor detects that the
    current heating tank temperature is too high (verified over 3 consecutive readings),
    the heating tank water pump will activate until the setpoint is met.
    """
    global heatingTankSetpoint, _heating_tank_setpoint_just_set
    global _heating_tank_consecutive_low, _heating_tank_consecutive_high
    try:
        val = float(setpoint)
    except (TypeError, ValueError):
        return
    heatingTankSetpoint = max(HEATING_TANK_MIN_SETPOINT, min(HEATING_TANK_MAX_SETPOINT, val))
    _heating_tank_setpoint_just_set = True
    _heating_tank_consecutive_low = 0
    _heating_tank_consecutive_high = 0
    print(f"[CONTROL] Heating tank setpoint updated -> {heatingTankSetpoint:.2f}C")


def applyHeatingTankControl(
    heating_tank_temp: float,
    setpoint: float = None,
    tolerance: float = HEATING_TANK_TEMP_TOLERANCE,
    consecutive_required: int = TEMP_CONSECUTIVE_REQUIRED,
):
    """
    Sensor-based heater and heating tank water pump control:
    - Compares the heating tank temperature sensor reading against the user-configured
      heating tank temperature setpoint.
    - If heating_tank_temp < setpoint - tolerance:
        Waits for 3 consecutive readings before activating heater relay to add more heat
        (and stops any cooldown pump override).
    - If heating_tank_temp >= setpoint:
        Deactivates heater relay to cool down and resets consecutive low counter.
    - Additional logic for setpoint change:
        When the setpoint has just been set and the heating tank sensor detects that the
        current heating tank temperature is too high (> setpoint) for 3 consecutive readings,
        activates the heating tank water pump until the heating tank temperature drops to <= setpoint.
    """
    global heatingTankSetpoint, _heating_tank_setpoint_just_set, _heating_pump_cooldown_active
    global _heating_tank_consecutive_low, _heating_tank_consecutive_high

    if maintenanceModeActive:
        return

    if setpoint is not None:
        try:
            heatingTankSetpoint = float(setpoint)
        except (TypeError, ValueError):
            pass

    target = float(heatingTankSetpoint)

    if heating_tank_temp is None:
        # Safety: if heating tank sensor cannot be read, reset counters and keep heater OFF
        _heating_tank_consecutive_low = 0
        _heating_tank_consecutive_high = 0
        _set_heater_relay(False)
        return

    temp_val = round(float(heating_tank_temp), 4)
    target_rounded = round(target, 4)
    low_threshold = round(target - float(tolerance), 4)

    if temp_val < low_threshold:
        _heating_tank_consecutive_low += 1
        _heating_tank_consecutive_high = 0
        _heating_tank_setpoint_just_set = False
        if _heating_pump_cooldown_active:
            with _pump_lock:
                _heating_pump_cooldown_active = False
                if _heating_pump_stop_timer is None:
                    heatingPump(False)
        if _heating_tank_consecutive_low >= consecutive_required or heaterActive:
            if not heaterActive:
                print(
                    f"[CONTROL] Heating tank temp LOW ({temp_val}C < {low_threshold}C, setpoint {target}C) "
                    f"[reading {_heating_tank_consecutive_low}/{consecutive_required}] - activating heater"
                )
            _set_heater_relay(True)
        else:
            print(
                f"[CONTROL] Heating tank temp LOW ({temp_val}C < {low_threshold}C, setpoint {target}C) "
                f"[reading {_heating_tank_consecutive_low}/{consecutive_required}] - "
                f"waiting for {consecutive_required} consecutive readings before activating heater"
            )
    else:
        _heating_tank_consecutive_low = 0
        if temp_val >= target_rounded:
            if heaterActive:
                print(
                    f"[CONTROL] Heating tank temp AT/ABOVE setpoint ({temp_val}C >= {target}C) - deactivating heater"
                )
            _set_heater_relay(False)

        if _heating_tank_setpoint_just_set:
            if temp_val > target_rounded:
                _heating_tank_consecutive_high += 1
                if _heating_tank_consecutive_high >= consecutive_required:
                    _heating_tank_setpoint_just_set = False
                    _heating_tank_consecutive_high = 0
                    with _pump_lock:
                        _heating_pump_cooldown_active = True
                        heatingPump(True)
                    print(
                        f"[CONTROL] Heating tank temp TOO HIGH after setpoint change ({temp_val}C > {target}C) "
                        f"[reading {consecutive_required}/{consecutive_required}] - "
                        f"activating heating tank water pump until setpoint is met"
                    )
                else:
                    print(
                        f"[CONTROL] Heating tank temp TOO HIGH after setpoint change ({temp_val}C > {target}C) "
                        f"[reading {_heating_tank_consecutive_high}/{consecutive_required}] - "
                        f"waiting for {consecutive_required} consecutive readings before activating heating tank water pump"
                    )
            else:
                _heating_tank_consecutive_high = 0
                _heating_tank_setpoint_just_set = False
                if _heating_pump_cooldown_active:
                    with _pump_lock:
                        _heating_pump_cooldown_active = False
                        if _heating_pump_stop_timer is None:
                            heatingPump(False)
                    print(
                        f"[CONTROL] Heating tank setpoint met ({temp_val}C <= {target}C) - deactivating heating tank water pump"
                    )
        elif _heating_pump_cooldown_active:
            _heating_tank_consecutive_high = 0
            if temp_val <= target_rounded:
                with _pump_lock:
                    _heating_pump_cooldown_active = False
                    if _heating_pump_stop_timer is None:
                        heatingPump(False)
                print(
                    f"[CONTROL] Heating tank setpoint met ({temp_val}C <= {target}C) - deactivating heating tank water pump"
                )
            else:
                with _pump_lock:
                    if not heatingPumpActive:
                        heatingPump(True)
        else:
            _heating_tank_consecutive_high = 0


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
    - temp >= setpoint + tolerance:
        Waits until 3 consecutive readings show that temperature has reached or exceeded threshold
        before triggering cooling system.
        Once triggered (or while cooling mode is active):
        Cooling valve ON (flow from cooling), heating & heating valve OFF.
        Records last active system as 'cooling'.
        Mixing tank control:
        If mixing tank <= setpoint - mixing_temp_limit for 3 continuous readings,
        cooling system is disabled while cooling valve remains OPEN.
        Cooling system turns back ON only after 3 continuous readings above this limit.
    - temp <= setpoint - tolerance:
        Waits until 3 consecutive readings show that temperature has reached or dropped below threshold
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

    if maintenanceModeActive:
        return
    high = round(setpoint + tolerance, 4)
    low  = round(setpoint - tolerance, 4)

    # Resolve secondary temperature if provided
    if secondary_temp is None:
        secondary_temp = read_secondary_temp_sensor()
    if secondary_temp is None:
        secondary_temp = round(temperature + random.uniform(-0.5, 0.5), 2)

    # Ensure main valve is always open for constant circulation
    if not mainValveOpen:
        mainValve(True)

    if round(temperature, 4) >= high:
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

            mix_cool_limit = round(setpoint - mixing_temp_limit, 4)

            if not _cooling_disabled_by_mixing:
                if round(secondary_temp, 4) <= mix_cool_limit:
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
                    print(f"[CONTROL] Tank temp HIGH ({temperature}C >= {high}C) [reading {_temp_consecutive_high}/{consecutive_required}] - cooling active (cooling valve ON, mixing tank {secondary_temp}C > {mix_cool_limit}C)")
            else:
                if round(secondary_temp, 4) > mix_cool_limit:
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
            print(f"[CONTROL] Tank temp HIGH ({temperature}C >= {high}C) [reading {_temp_consecutive_high}/{consecutive_required}] - waiting for {consecutive_required} consecutive readings before triggering cooling")
            mainValve(True)

    elif round(temperature, 4) <= low:
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

            mix_heat_limit = round(setpoint + mixing_temp_limit, 4)

            if not _heating_disabled_by_mixing:
                if round(secondary_temp, 4) >= mix_heat_limit:
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
                    print(f"[CONTROL] Tank temp LOW ({temperature}C <= {low}C) [reading {_temp_consecutive_low}/{consecutive_required}] - activating heating (heating valve ON, mixing tank {secondary_temp}C < {mix_heat_limit}C)")
            else:
                if round(secondary_temp, 4) < mix_heat_limit:
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
            print(f"[CONTROL] Tank temp LOW ({temperature}C <= {low}C) [reading {_temp_consecutive_low}/{consecutive_required}] - waiting for {consecutive_required} consecutive readings before triggering heating")
            mainValve(True)

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

    if maintenanceModeActive:
        return
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
        print(f"[CONTROL] DO RECOVERING ({do_level} mg/L within -{tolerance} mg/L of setpoint {setpoint} mg/L) - maintaining {_dosvcounter}/{DO_SOLENOID_VALVE_COUNT} open valves, bleed valve {doBleedValvePercent:.1f}%")
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
_temp_heating_device_file = None

if isRpiPresent:
    import os as _os
    import glob as _glob

    _os.system("modprobe w1-gpio")
    _os.system("modprobe w1-therm")

    _base_dir = "/sys/bus/w1/devices/"
    _main_id = "28-000000b197bd"
    _mixing_id = "28-000000b260f4"
    _heating_id = _os.environ.get("HEATING_TANK_DEVICE_ID", "").strip() or "28-7cf4d4452c40"

    _temp_device_file = _base_dir + _main_id + "/w1_slave"
    _temp_secondary_device_file = _base_dir + _mixing_id + "/w1_slave"
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

    if _heating_id and _os.path.exists(_base_dir + _heating_id + "/w1_slave"):
        _temp_heating_device_file = _base_dir + _heating_id + "/w1_slave"
    else:
        _discovered = sorted(_glob.glob(_base_dir + "28-*"))
        _extra = [
            d + "/w1_slave"
            for d in _discovered
            if _os.path.basename(d) not in (_main_id, _mixing_id)
            and _os.path.exists(d + "/w1_slave")
        ]
        if _extra:
            _temp_heating_device_file = _extra[0]

    if _temp_heating_device_file is not None:
        print(f"[RPI] DS18B20 Heating tank sensor found: {_temp_heating_device_file}")
    else:
        print("[RPI] DS18B20 Heating tank sensor not found - Heating tank temperature will use simulated value")


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


def read_heating_tank_temp_sensor():
    """
    Read temperature (C) from the Heating tank DS18B20 1-Wire sensor.
    Returns a float on RPi when the sensor is present and valid,
    or None otherwise (then use simulated value). Rejects invalid 85.0C reading.
    """
    if not isRpiPresent or _temp_heating_device_file is None:
        print("[DUMMY] Heating tank temperature sensor read - using simulated value")
        return None
    try:
        val = _parse_ds18b20(_temp_heating_device_file, "heating_tank")
        if val is not None and abs(val - 85.0) < 0.01:
            print("[RPI] Heating tank DS18B20 rejected invalid 85.0C reading")
            return None
        return val
    except Exception as e:
        print(f"[RPI] Heating tank DS18B20 read failed ({e}) - using simulated value")
        return None


# ---------------------------------------------------------------------------
# Maintenance Mode (Isolated Manual Component Testing & State Reversion)
# ---------------------------------------------------------------------------

maintenanceModeActive: bool = False
_maintenance_lock = threading.RLock()
_maintenance_snapshot = None
_maintenance_components = {
    "heater": False,
    "heating_valve": False,
    "cooling_valve": True,
    "peltiers": False,
    "aerator_valve_1": True,
    "aerator_valve_2": True,
    "bleed_valve": 0,
    "heating_pump": False,
    "cooling_pump": False,
    "mixing_fan": False,
}


def _nearest_bleed_valve_level(percent: float) -> int:
    """Map a percentage (0..100) to the nearest index in BLEED_VALVE_POSITIONS."""
    clamped = max(0.0, min(100.0, float(percent)))
    return min(
        range(len(BLEED_VALVE_POSITIONS)),
        key=lambda i: abs(BLEED_VALVE_POSITIONS[i] - clamped),
    )


def _validate_bleed_valve_opening(value) -> tuple[int, int]:
    """
    Validate a discrete bleed valve opening (0, 33, 66, or 100).
    Returns (level_index, opening_int). Raises ValueError for invalid values.
    """
    if isinstance(value, bool):
        raise ValueError("Bleed valve opening must be one of 0, 33, 66, 100")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Bleed valve opening must be one of 0, 33, 66, 100") from exc

    for idx, pos in enumerate(BLEED_VALVE_POSITIONS):
        if abs(numeric - pos) < 1e-6:
            return idx, int(round(pos))
    raise ValueError("Bleed valve opening must be one of 0, 33, 66, 100")


def _step_bleed_valve_motor(direction, total_steps: int):
    """Step the bleed valve stepper motor driver by total_steps in the given direction."""
    if not (isRpiPresent and GPIO is not None):
        return
    with _valve_motor_lock:
        try:
            GPIO.output(bleedValveEnablePin, GPIO.LOW)
            GPIO.output(bleedValveDirPin, direction)
            for _ in range(total_steps):
                GPIO.output(bleedValveStepPin, GPIO.HIGH)
                time.sleep(STEP_DELAY)
                GPIO.output(bleedValveStepPin, GPIO.LOW)
                time.sleep(STEP_DELAY)
        except Exception as e:
            logging.error(f"[RPI] GPIO failure during maintenance bleed valve movement: {e}")
            print(f"[RPI] GPIO failure during maintenance bleed valve movement: {e}")


def is_maintenance_mode() -> bool:
    """Return True if the system is currently in isolated manual maintenance mode."""
    return bool(maintenanceModeActive)


def _current_normal_component_states() -> dict:
    """Read the current normal-mode hardware states for all maintenance-controllable components."""
    bleed_level = _nearest_bleed_valve_level(doBleedValvePercent)
    return {
        "heater": bool(heaterActive),
        "heating_valve": bool(heatingValveOpen),
        "cooling_valve": bool(coolingValveOpen),
        "peltiers": bool(coolingSystemActive),
        "aerator_valve_1": bool(_dosvcounter >= 1),
        "aerator_valve_2": bool(_dosvcounter >= 2),
        "bleed_valve": int(round(BLEED_VALVE_POSITIONS[bleed_level])),
        "heating_pump": bool(heatingPumpActive),
        "cooling_pump": bool(coolingPumpActive),
        "mixing_fan": bool(mixerActive),
    }


def get_maintenance_state() -> dict:
    """
    Return the current maintenance mode status and component states.
    When maintenance mode is active, returns the isolated maintenance component states;
    when inactive, returns the live normal-mode component states.
    """
    with _maintenance_lock:
        components = (
            dict(_maintenance_components)
            if maintenanceModeActive
            else _current_normal_component_states()
        )
        return {
            "active": bool(maintenanceModeActive),
            "components": components,
        }


def _apply_maintenance_heater(active: bool):
    state = bool(active)
    _maintenance_components["heater"] = state
    if isRpiPresent and GPIO is not None:
        GPIO.output(heatingSystemPin, GPIO.HIGH if state else GPIO.LOW)
        print(f"[MAINTENANCE][RPI] heater relay (GPIO {heatingSystemPin}) -> {'ON' if state else 'OFF'}")
    else:
        print(f"[MAINTENANCE][DUMMY] heater relay -> {'ON' if state else 'OFF'}")


def _apply_maintenance_heating_valve(is_open: bool):
    state = bool(is_open)
    if state == _maintenance_components["heating_valve"]:
        return
    direction = CCW if state else CW
    action_str = "OPEN (CCW)" if state else "CLOSED (CW)"
    if isRpiPresent and GPIO is not None:
        print(f"[MAINTENANCE][RPI] heating valve stepper -> {action_str} {VALVE_STEP_AMOUNT} steps")
        _step_valve_motor(heatValveEnablePin, heatValveDirPin, heatValvePulPin, direction, VALVE_STEP_AMOUNT)
    else:
        print(f"[MAINTENANCE][DUMMY] heating valve stepper -> {action_str} {VALVE_STEP_AMOUNT} steps")
    _maintenance_components["heating_valve"] = state
    try:
        from app.models import save_motor_state
        save_motor_state("heating_valve", 1 if state else 0)
    except Exception:
        pass


def _apply_maintenance_cooling_valve(is_open: bool):
    state = bool(is_open)
    if state == _maintenance_components["cooling_valve"]:
        return
    direction = CCW if state else CW
    action_str = "OPEN (CCW)" if state else "CLOSED (CW)"
    if isRpiPresent and GPIO is not None:
        print(f"[MAINTENANCE][RPI] cooling valve stepper -> {action_str} {VALVE_STEP_AMOUNT} steps")
        _step_valve_motor(coolValveEnablePin, coolValveDirPin, coolValvePulPin, direction, VALVE_STEP_AMOUNT)
    else:
        print(f"[MAINTENANCE][DUMMY] cooling valve stepper -> {action_str} {VALVE_STEP_AMOUNT} steps")
    _maintenance_components["cooling_valve"] = state
    try:
        from app.models import save_motor_state
        save_motor_state("cooling_valve", 1 if state else 0)
    except Exception:
        pass


def _apply_maintenance_peltiers(active: bool):
    state = bool(active)
    _maintenance_components["peltiers"] = state
    if isRpiPresent and GPIO is not None:
        for pin in PELTIER_PINS:
            GPIO.output(pin, GPIO.LOW if state else GPIO.HIGH)
        print(f"[MAINTENANCE][RPI] peltier modules (all {len(PELTIER_PINS)}) -> {'ON' if state else 'OFF'}")
    else:
        print(f"[MAINTENANCE][DUMMY] peltier modules (all {len(PELTIER_PINS)}) -> {'ON' if state else 'OFF'}")


def _apply_maintenance_aerator_valve(valve_num: int, is_open: bool):
    state = bool(is_open)
    if valve_num == 1:
        _maintenance_components["aerator_valve_1"] = state
        pin = doSolenoidValve1
    elif valve_num == 2:
        _maintenance_components["aerator_valve_2"] = state
        pin = doSolenoidValve2
    else:
        raise ValueError(f"Invalid aerator valve number: {valve_num}")
    if isRpiPresent and GPIO is not None:
        GPIO.output(pin, GPIO.LOW if state else GPIO.HIGH)
        print(f"[MAINTENANCE][RPI] aerator solenoid valve {valve_num} (GPIO {pin}) -> {'OPEN' if state else 'CLOSED'}")
    else:
        print(f"[MAINTENANCE][DUMMY] aerator solenoid valve {valve_num} -> {'OPEN' if state else 'CLOSED'}")


def _apply_maintenance_heating_pump(active: bool):
    state = bool(active)
    _maintenance_components["heating_pump"] = state
    if isRpiPresent and GPIO is not None:
        GPIO.output(heatingPumpPin, GPIO.LOW if state else GPIO.HIGH)
        print(f"[MAINTENANCE][RPI] heating tank water pump (GPIO {heatingPumpPin}) -> {'ON' if state else 'OFF'}")
    else:
        print(f"[MAINTENANCE][DUMMY] heating tank water pump -> {'ON' if state else 'OFF'}")


def _apply_maintenance_cooling_pump(active: bool):
    state = bool(active)
    _maintenance_components["cooling_pump"] = state
    if isRpiPresent and GPIO is not None:
        GPIO.output(coolingPumpPin, GPIO.LOW if state else GPIO.HIGH)
        print(f"[MAINTENANCE][RPI] cooling tank water pump (GPIO {coolingPumpPin}) -> {'ON' if state else 'OFF'}")
    else:
        print(f"[MAINTENANCE][DUMMY] cooling tank water pump -> {'ON' if state else 'OFF'}")


def _apply_maintenance_mixing_fan(active: bool):
    state = bool(active)
    _maintenance_components["mixing_fan"] = state
    if isRpiPresent and GPIO is not None:
        GPIO.output(mixerPin, GPIO.LOW if state else GPIO.HIGH)
        print(f"[MAINTENANCE][RPI] mixing fan (GPIO {mixerPin}) -> {'ON' if state else 'OFF'}")
    else:
        print(f"[MAINTENANCE][DUMMY] mixing fan -> {'ON' if state else 'OFF'}")


def _apply_maintenance_bleed_valve(target_percent):
    target_level, target_opening = _validate_bleed_valve_opening(target_percent)
    current_level = _nearest_bleed_valve_level(_maintenance_components.get("bleed_valve", 0))
    if current_level == target_level:
        _maintenance_components["bleed_valve"] = target_opening
        return

    level_diff = target_level - current_level
    direction = CCW if level_diff > 0 else CW
    total_steps = abs(level_diff) * STEPS_PER_POSITION
    direction_str = "CCW" if level_diff > 0 else "CW"

    if isRpiPresent and GPIO is not None:
        print(f"[MAINTENANCE][RPI] bleed valve stepper -> {target_opening}% ({direction_str}, {total_steps} steps)")
        _step_bleed_valve_motor(direction, total_steps)
    else:
        print(f"[MAINTENANCE][DUMMY] bleed valve stepper -> {target_opening}% ({direction_str}, {total_steps} steps)")

    _maintenance_components["bleed_valve"] = target_opening
    try:
        from app.models import save_motor_state
        save_motor_state("bleed_valve", target_level)
    except Exception:
        pass


def enter_maintenance_mode() -> dict:
    """
    Enter isolated maintenance mode:
    - Snapshots all normal-mode hardware and automated control states.
    - Cancels pending automated timers (mixer shutdown, pump start/stop) so they do not fire.
    - Pauses automated control loops until exit_maintenance_mode() is called.
    Idempotent if already in maintenance mode (preserves original normal-mode snapshot).
    """
    global maintenanceModeActive, _maintenance_snapshot, _mixer_timer
    with _maintenance_lock:
        if maintenanceModeActive:
            return get_maintenance_state()

        # Cancel pending automated timers so they cannot override manual testing
        with _mixer_lock:
            if _mixer_timer is not None:
                try:
                    _mixer_timer.cancel()
                except Exception:
                    pass
                _mixer_timer = None
        cancel_pump_timers()

        normal_components = _current_normal_component_states()
        _maintenance_snapshot = {
            "components": dict(normal_components),
            "heatingSystemActive": bool(heatingSystemActive),
            "coolingSystemActive": bool(coolingSystemActive),
            "heaterActive": bool(heaterActive),
            "heatingValveOpen": bool(heatingValveOpen),
            "coolingValveOpen": bool(coolingValveOpen),
            "heatingPumpActive": bool(heatingPumpActive),
            "coolingPumpActive": bool(coolingPumpActive),
            "mixerActive": bool(mixerActive),
            "mixingFanActive": bool(mixingFanActive),
            "dosvcounter": int(_dosvcounter),
            "doSolenoidValveOpen": bool(doSolenoidValveOpen),
            "doBleedValvePercent": float(doBleedValvePercent),
            "aeratorFanActive": bool(aeratorFanActive),
            "last_temp_system": last_temp_system,
            "_heating_disabled_by_mixing": bool(_heating_disabled_by_mixing),
            "_cooling_disabled_by_mixing": bool(_cooling_disabled_by_mixing),
            "_heating_pump_cooldown_active": bool(_heating_pump_cooldown_active),
        }

        _maintenance_components.clear()
        _maintenance_components.update(normal_components)
        maintenanceModeActive = True
        print("[MAINTENANCE] Entered maintenance mode - automated controls paused, normal state snapshotted")
        return get_maintenance_state()


def exit_maintenance_mode() -> dict:
    """
    Exit maintenance mode and revert all physical hardware states and software flags
    back to the exact pre-maintenance normal-mode snapshot.
    """
    global maintenanceModeActive, _maintenance_snapshot
    global heatingSystemActive, coolingSystemActive, heaterActive
    global heatingValveOpen, coolingValveOpen
    global heatingPumpActive, coolingPumpActive, mixerActive, mixingFanActive
    global _dosvcounter, doSolenoidValveOpen, doBleedValvePercent, aeratorFanActive, aeratorCoolerFanActive
    global last_temp_system, _heating_disabled_by_mixing, _cooling_disabled_by_mixing
    global _heating_pump_cooldown_active

    with _maintenance_lock:
        if not maintenanceModeActive:
            return get_maintenance_state()

        snapshot = _maintenance_snapshot or {}
        saved = snapshot.get("components", _current_normal_component_states())

        # 1. Revert stepper valves physically ONLY if their physical position changed
        _apply_maintenance_heating_valve(saved["heating_valve"])
        _apply_maintenance_cooling_valve(saved["cooling_valve"])
        _apply_maintenance_bleed_valve(saved.get("bleed_valve", 0))

        # 2. Revert heater relay
        _apply_maintenance_heater(saved["heater"])

        # 3. Revert all 8 peltier modules
        _apply_maintenance_peltiers(saved["peltiers"])

        # 4. Revert aerator solenoid valves 1 & 2
        _apply_maintenance_aerator_valve(1, saved["aerator_valve_1"])
        _apply_maintenance_aerator_valve(2, saved["aerator_valve_2"])

        # 5. Revert heating & cooling tank water pumps
        _apply_maintenance_heating_pump(saved["heating_pump"])
        _apply_maintenance_cooling_pump(saved["cooling_pump"])

        # 6. Revert mixing fan
        _apply_maintenance_mixing_fan(saved["mixing_fan"])

        # 7. Restore normal-mode software state variables from snapshot
        heatingSystemActive = snapshot.get("heatingSystemActive", False)
        coolingSystemActive = snapshot.get("coolingSystemActive", saved["peltiers"])
        heaterActive = snapshot.get("heaterActive", saved["heater"])
        heatingValveOpen = snapshot.get("heatingValveOpen", saved["heating_valve"])
        coolingValveOpen = snapshot.get("coolingValveOpen", saved["cooling_valve"])
        heatingPumpActive = snapshot.get("heatingPumpActive", saved["heating_pump"])
        coolingPumpActive = snapshot.get("coolingPumpActive", saved["cooling_pump"])
        mixerActive = snapshot.get("mixerActive", saved["mixing_fan"])
        mixingFanActive = snapshot.get("mixingFanActive", saved["mixing_fan"])
        _dosvcounter = snapshot.get("dosvcounter", _dosvcounter)
        doSolenoidValveOpen = snapshot.get("doSolenoidValveOpen", _dosvcounter > 0)
        doBleedValvePercent = float(snapshot.get("doBleedValvePercent", saved.get("bleed_valve", 0)))
        aeratorFanActive = snapshot.get("aeratorFanActive", _dosvcounter > 0)
        aeratorCoolerFanActive = aeratorFanActive
        last_temp_system = snapshot.get("last_temp_system", last_temp_system)
        _heating_disabled_by_mixing = snapshot.get("_heating_disabled_by_mixing", False)
        _cooling_disabled_by_mixing = snapshot.get("_cooling_disabled_by_mixing", False)
        _heating_pump_cooldown_active = snapshot.get("_heating_pump_cooldown_active", False)

        maintenanceModeActive = False
        _maintenance_snapshot = None
        print("[MAINTENANCE] Exited maintenance mode - all component states reverted to normal operation")
        return get_maintenance_state()


def set_maintenance_component(component: str, state) -> dict:
    """
    Manually control an individual hardware component while in maintenance mode.
    Supported components:
      - 'heater'            : ON (True) / OFF (False)
      - 'heating_valve'     : OPEN (True) / CLOSED (False)
      - 'cooling_valve'     : OPEN (True) / CLOSED (False)
      - 'peltiers'          : ON (True) / OFF (False) for all 8 peltier modules at once
      - 'aerator_valve_1'   : OPEN (True) / CLOSED (False) for 1st aerator solenoid valve
      - 'aerator_valve_2'   : OPEN (True) / CLOSED (False) for 2nd aerator solenoid valve
      - 'aerator_valves_both': OPEN (True) / CLOSED (False) for both aerator solenoid valves
      - 'bleed_valve'       : discrete opening percentage (0, 33, 66, or 100)
      - 'heating_pump'      : ON (True) / OFF (False) for heating tank water pump
      - 'cooling_pump'      : ON (True) / OFF (False) for cooling tank water pump
      - 'mixing_fan'        : ON (True) / OFF (False) for mixing fan
    """
    with _maintenance_lock:
        if not maintenanceModeActive:
            raise RuntimeError("Maintenance mode is not active")

        if component == "bleed_valve":
            _apply_maintenance_bleed_valve(state)
            return get_maintenance_state()

        desired = bool(state)
        if component == "heater":
            _apply_maintenance_heater(desired)
        elif component == "heating_valve":
            _apply_maintenance_heating_valve(desired)
        elif component == "cooling_valve":
            _apply_maintenance_cooling_valve(desired)
        elif component == "peltiers":
            _apply_maintenance_peltiers(desired)
        elif component == "aerator_valve_1":
            _apply_maintenance_aerator_valve(1, desired)
        elif component == "aerator_valve_2":
            _apply_maintenance_aerator_valve(2, desired)
        elif component in ("aerator_valves_both", "aerator_both"):
            _apply_maintenance_aerator_valve(1, desired)
            _apply_maintenance_aerator_valve(2, desired)
        elif component == "heating_pump":
            _apply_maintenance_heating_pump(desired)
        elif component == "cooling_pump":
            _apply_maintenance_cooling_pump(desired)
        elif component == "mixing_fan":
            _apply_maintenance_mixing_fan(desired)
        else:
            raise ValueError(f"Unknown maintenance component: {component}")

        return get_maintenance_state()


def cleanupGpio():
    """
    Disable stepper motor driver (ENABLE pin HIGH), turn off heater relay,
    cancel mixer and water pump timers, turn off pumps and mixer, and cleanup GPIO pins on shutdown.
    """
    global bleedValveDriverEnabled, _mixer_timer, heatingSystemActive, _heating_pump_cooldown_active
    global pigpio_pi, ledPwm, mosfet, maintenanceModeActive, _maintenance_snapshot
    maintenanceModeActive = False
    _maintenance_snapshot = None
    if _mixer_timer is not None:
        try:
            _mixer_timer.cancel()
            _mixer_timer = None
        except Exception:
            pass

    cancel_pump_timers()
    _heating_pump_cooldown_active = False

    with _heating_lock:
        heatingSystemActive = False
        _set_heater_relay(False)

    heatingPump(False)
    coolingPump(False)
    coolingSystem(False)
    mixer(False)
    mainValve(False)
    setAeratorFan(False)

    if isRpiPresent and GPIO is not None:
        try:
            print("[RPI] Closing source valve...")
            GPIO.output(sourceValvePin, GPIO.HIGH)
        except Exception as e:
            logging.warning(f"[RPI] Exception while resetting source valve: {e}")
        try:
            print("[RPI] Disabling heating and cooling valve stepper drivers...")
            GPIO.output(heatValveEnablePin, MOTOR_DISABLE)
            GPIO.output(coolValveEnablePin, MOTOR_DISABLE)
        except Exception as e:
            logging.warning(f"[RPI] Exception while disabling valve stepper drivers: {e}")
        try:
            print("[RPI] Turning off peltier cooling elements (8 modules)...")
            for pin in PELTIER_PINS:
                GPIO.output(pin, GPIO.HIGH)
        except Exception as e:
            logging.warning(f"[RPI] Exception while resetting cooling peltiers: {e}")
        try:
            print("[RPI] Closing aerator solenoid valves (2 valves)...")
            GPIO.output(doSolenoidValve1, GPIO.HIGH)
            GPIO.output(doSolenoidValve2, GPIO.HIGH)
        except Exception as e:
            logging.warning(f"[RPI] Exception while resetting aerator valves: {e}")
        try:
            print("[RPI] Turning off heating and cooling tank water pumps...")
            GPIO.output(heatingPumpPin, GPIO.HIGH)
            GPIO.output(coolingPumpPin, GPIO.HIGH)
        except Exception as e:
            logging.warning(f"[RPI] Exception while resetting water pumps: {e}")
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
