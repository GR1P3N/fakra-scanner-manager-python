#!/usr/bin/env python3
import time
import datetime
import subprocess
import sys
import signal

CHIP   = "gpiochip0"
OFFSET = "144"
DELAY  = 1.0  # másodpercenként lekérdez

def get_gpio_state():
    try:
        # gpioget <chip> <offset> → "0" vagy "1"
        r = subprocess.run(
            ["gpioget", CHIP, OFFSET],
            capture_output=True, text=True, check=True
        )
        return r.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Hiba a GPIO-olvasásnál: {e}", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("Hiányzik a gpioget: telepítsd a libgpiod-tools csomagot.", file=sys.stderr)
        sys.exit(1)

def signal_handler(sig, frame):
    print("\nLeállítás…")
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, signal_handler)
    print(f"Figyelem: {CHIP} line {OFFSET} figyelés indítva. Ctrl+C a kilépéshez.\n")
    while True:
        state = get_gpio_state()
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if state == "1":
            desc = "MAGAS"
        elif state == "0":
            desc = "ALACSONY"
        else:
            desc = f"ISMERETLEN ({state})"
        print(f"[{ts}] GPIO#{OFFSET} → {desc}")
        time.sleep(DELAY)

if __name__ == "__main__":
    main()
