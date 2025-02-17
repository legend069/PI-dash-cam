import RPi.GPIO as GPIO
import time
import os
import hashlib
import threading
from datetime import datetime
import sys
from datetime import timedelta

LED_RECORDING = 15
LED_TRANSFER = 2
BUTTON_LOCK = 14

def log_message(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    message = f"{timestamp} - {message}"
    if sys.stdout.isatty():
        print(message)
    else:
        with open(log_file, "a") as log:
            log.write(message + "\n")

def cleanup():
    GPIO.output(LED_RECORDING, GPIO.LOW) #low set to GND
    GPIO.output(LED_TRANSFER, GPIO.LOW)  #high set to 3.3
    GPIO.cleanup()
    log_message("GPIO cleaned up.")

def setup():
    GPIO.setmode(GPIO.BCM)      # use PHYSICAL GPIO Numbering

    GPIO.setup(LED_RECORDING, GPIO.OUT)
    GPIO.setup(LED_TRANSFER, GPIO.OUT)
    GPIO.setup(BUTTON_LOCK, GPIO.IN, pull_up_down=GPIO.PUD_UP)    
    log_message("setup completed")


if __name__ == "__main__":

    setup()
    try:
        while True:

            state = GPIO.input(BUTTON_LOCK)
            log_message(f"state: {state}")
            if state:
                log_message("led on")
                GPIO.output(LED_RECORDING, GPIO.HIGH)
            else:
                log_message("led off")
                GPIO.output(LED_RECORDING, GPIO.LOW)
            #time.sleep(0.5)

    except KeyboardInterrupt:
        cleanup()
