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
ROOM, LEVEL  = "A109", "level0"

CLEAN_AIR_V = 0.10
FIRE_V      = 3.00
RAMP_S      = 20.0
RAMP_STEP_S = 1.0

VISIBLE_FROM = 0.30      # volts below which nothing is drawn (still clean air)


def set_effects(volts):
    """Draw the fire in the 3D viewer, scaled by how bad it is.

    /api/effects is a REPLACE-the-whole-collection endpoint: whatever list you
    send becomes the complete set, and [] clears everything. BuildSim animates
    the primitives but models no physics — spreading and dying down is our job,
    which is what the intensity below does.
    """
    intensity = max(0.0, min(1.0, (volts - VISIBLE_FROM) / (FIRE_V - VISIBLE_FROM)))

    if intensity <= 0.0:
        effects = []                       # clean air: draw nothing
    else:
        effects = [
            {"id": f"fire-{ROOM}", "type": "fire", "label": "Fire source",
             "level": LEVEL, "room": ROOM,
             "radius": 3 + 4 * intensity,   # grows as it gets worse
             "height": 6 + 9 * intensity,
             "intensity": round(intensity, 2)},
            {"id": f"smoke-{ROOM}", "type": "smoke",
             "level": LEVEL, "room": ROOM,
             "radius": 4 + 4 * intensity,
             "height": 8 + 10 * intensity,
             "intensity": round(intensity, 2)},
        ]

    r = requests.put(f"{BUILDSIM}/api/effects", json=effects, timeout=2.0)
    r.raise_for_status()
    return intensity


def set_smoke(volts):
    """PUT one smoke value into the twin, and update what the viewer draws.

    Two separate things, deliberately:
      - the SENSOR value is what the Pi reads and acts on   (the data)
      - the EFFECT is what a human sees on the floor plan   (the picture)
    Keeping them apart matters: the agent must decide from the reading, never
    from the visualisation.
    """
    r = requests.put(f"{BUILDSIM}/api/sensors/{SMOKE_VAL_ID}/value",
                     json={"data_type": "text", "value": f"{volts:.3f}"},
                     timeout=2.0)
    r.raise_for_status()

    intensity = set_effects(volts)
    print(f"smoke = {volts:.3f} V   fire intensity = {intensity:.2f}")


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
