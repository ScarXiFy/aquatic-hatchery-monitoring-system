import RPi.GPIO as GPIO
from time import sleep

# Use BCM numbering to match most pinout diagrams
GPIO.setmode(GPIO.BCM)

PWM_PIN = 13
GPIO.setup(PWM_PIN, GPIO.OUT)

# Initialize PWM at 1000Hz (a good frequency for flicker-free LEDs)
mosfet = GPIO.PWM(PWM_PIN, 1000)
mosfet.start(0)  # Start at 0% brightness

MAX_LUX = 3000

def set_lux(target_lux):
    """
    Converts a target lux value into a PWM duty cycle (0 to 100)
    and applies it to the MOSFET using RPi.GPIO.
    """
    # Calculate the percentage (0 to 100)
    duty_cycle = (target_lux / MAX_LUX) * 100
    
    # Clamp the value to prevent library errors
    duty_cycle = max(0.0, min(100.0, duty_cycle))
    
    # Set the MOSFET PWM output
    mosfet.ChangeDutyCycle(duty_cycle)
    print(f"Lights set to {target_lux} lux ({duty_cycle:.1f}% power)")

try:
    print("Starting hatchery lighting test...")
    hatchery_levels = [0, 100, 500, 1000, 3000]
    
    for lux in hatchery_levels:
        set_lux(lux)
        sleep(5)
        
    print("Test complete. Holding at max brightness.")

except KeyboardInterrupt:
    print("\nScript interrupted. Turning off lights.")

finally:
    # Safely shut down the PWM signal and clean up only the LED pin
    mosfet.stop()
    GPIO.cleanup(PWM_PIN)