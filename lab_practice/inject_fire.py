"""
inject_fire.py — set the simulated smoke level in the BuildSim twin.

This is the fault-injection handle for Scenario B. The Pi cannot read a real
smoke sensor (no ADC), so smoke lives in the twin; this script is what makes
it rise and fall. real_hardware.read_smoke() picks the value straight up, and
the agent's response drives the REAL buzzer on the desk.

    cyber-side cause  ->  physical effect

Usage (run anywhere that can reach BuildSim):

    python3 inject_fire.py on        # smoke -> 3.0 V  (fire)
    python3 inject_fire.py off       # smoke -> 0.1 V  (clean air)
    python3 inject_fire.py 1.4       # any level you like
    python3 inject_fire.py ramp      # 0.1 -> 3.0 V over 20 s, like a real fire

If BuildSim is not on this machine:

    export BUILDSIM_URL=http://192.168.1.44:9090
"""
import os
import sys
import time

import requests

BUILDSIM     = os.environ.get("BUILDSIM_URL", "http://localhost:9090")
SMOKE_VAL_ID = "pi-smoke-A109-val"

CLEAN_AIR_V = 0.10
FIRE_V      = 3.00
RAMP_S      = 20.0
RAMP_STEP_S = 1.0


def set_smoke(volts):
    """PUT one smoke value into the twin."""
    r = requests.put(f"{BUILDSIM}/api/sensors/{SMOKE_VAL_ID}/value",
                     json={"data_type": "text", "value": f"{volts:.3f}"},
                     timeout=2.0)
    r.raise_for_status()
    print(f"smoke = {volts:.3f} V")


def ramp():
    """Rise from clean air to full fire, so the agent sees a trend and not a jump."""
    steps = int(RAMP_S / RAMP_STEP_S)
    for i in range(steps + 1):
        set_smoke(CLEAN_AIR_V + (FIRE_V - CLEAN_AIR_V) * (i / steps))
        time.sleep(RAMP_STEP_S)


def main():
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if arg == "on":
        set_smoke(FIRE_V)
    elif arg == "off":
        set_smoke(CLEAN_AIR_V)
    elif arg == "ramp":
        ramp()
    else:
        try:
            set_smoke(float(arg))
        except ValueError:
            print(__doc__)
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        print(f"BuildSim unreachable at {BUILDSIM}: {e}")
        print("Is BuildSim running, and has real_hardware.py registered the "
              "sensor at least once?")
        sys.exit(1)
