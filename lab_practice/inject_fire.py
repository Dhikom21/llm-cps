"""
inject_fire.py — set the simulated smoke level in a room of the twin.

To study how a system behaves in an emergency you need emergencies on demand.
Setting fire to the lab is not an option, so smoke lives in the twin and this
is the handle that moves it. That is fault injection: creating the abnormal
condition deliberately, under control, so the response can be observed and —
more importantly — repeated identically for each agent under test.

Usage:
    python3 inject_fire.py on              # A109 -> 3.0 V
    python3 inject_fire.py off             # A109 -> clean air
    python3 inject_fire.py ramp            # A109 climbs over 20 s
    python3 inject_fire.py ramp A108       # any configured room
    python3 inject_fire.py 1.4 A108        # an exact level
    python3 inject_fire.py off all         # clear every room

Why a ramp as well as a switch:
    `on` jumps instantly and tests whether an agent reacts to a THRESHOLD.
    `ramp` rises over 20 s and tests whether it spots a TREND and acts early —
    a harder question, and a much more interesting one to ask a language model.
    Having both lets you ask them separately.

If BuildSim is elsewhere:
    export BUILDSIM_URL=http://192.168.1.45:9090
"""
import os
import sys
import time

import requests

import pi_guard
import rooms
import twin

CLEAN_AIR_V = 0.10
FIRE_V      = 3.00

# How long the ramp takes, and how often it steps.
#
# These must be set against the SAMPLING rate of the system, not chosen for
# convenience. The agent only ever sees values that pi_sensor.py happened to
# publish while the ramp was passing through them, so a ramp faster than a few
# sensing cycles is invisible: the agent observes clean air, then 3.0 V, and
# nothing in between.
#
# That turns a trend experiment into a threshold experiment without anyone
# noticing. Rule of thumb: the ramp should span at least five of whatever the
# slowest link is — usually the agent's cycle, not the sensor's.
#
#     RAMP_S=20    fast step. Tests reaction to a THRESHOLD being crossed.
#     RAMP_S=180   slow rise. Tests whether a TREND is detected and acted on
#                  BEFORE the threshold, which is the harder question.
RAMP_S      = float(os.environ.get("RAMP_S", "180"))
RAMP_STEP_S = float(os.environ.get("RAMP_STEP_S", "3"))

VISIBLE_FROM = 0.30      # below this, nothing is drawn — still clean air

# Which room the viewer is currently showing a fire in. /api/effects replaces
# the whole collection on every write, so this module has to remember what it
# drew last or it would erase another room's fire.
_active = {}


def set_effects():
    """Redraw every active fire, scaled by how bad each one is.

    BuildSim animates the primitives but models no physics — it will not spread
    fire or diffuse smoke. Growth is ours to express, which is what the
    intensity-scaled radius and height below do.
    """
    effects = []
    for room, volts in sorted(_active.items()):
        intensity = max(0.0, min(1.0,
                                 (volts - VISIBLE_FROM) / (FIRE_V - VISIBLE_FROM)))
        if intensity <= 0.0:
            continue
        level = rooms.level(room)
        effects += [
            {"id": f"fire-{room}", "type": "fire", "label": f"Fire in {room}",
             "level": level, "room": room,
             "radius": 3 + 4 * intensity, "height": 6 + 9 * intensity,
             "intensity": round(intensity, 2)},
            {"id": f"smoke-{room}", "type": "smoke",
             "level": level, "room": room,
             "radius": 4 + 4 * intensity, "height": 8 + 10 * intensity,
             "intensity": round(intensity, 2)},
        ]

    r = requests.put(f"{twin.BUILDSIM}/api/effects", json=effects, timeout=2.0)
    r.raise_for_status()


_crossed = set()


def _mark_crossing(room, volts):
    """Record the instant smoke crosses the threshold, once per fire.

    This is the start of the clock for response-time measurement. Without a
    marker written by the INJECTOR, response time has to be inferred from when
    the agent happened to notice — which measures the agent's sampling, not the
    system's response, and flatters whichever configuration samples faster.
    """
    above = volts >= rooms.SMOKE_THRESHOLD
    if above and room not in _crossed:
        _crossed.add(room)
        pi_guard.latest_run()
        pi_guard.audit({"source": "injector", "kind": "threshold_crossed",
                        "room": room, "smoke_v": volts,
                        "threshold": rooms.SMOKE_THRESHOLD})
        print(f"  ^ crossed {rooms.SMOKE_THRESHOLD} V — clock started")
    elif not above:
        _crossed.discard(room)


def set_smoke(room, volts):
    """Two separate things, deliberately kept apart:

        the SENSOR value is what the agent reads and acts on   (the data)
        the EFFECT is what a human sees on the floor plan      (the picture)

    The agent must decide from the reading, never from the visualisation, and
    putting them on different endpoints makes that structurally true rather
    than merely intended.
    """
    twin.set_smoke(room, volts)
    _active[room] = volts
    set_effects()
    print(f"{room}: smoke = {volts:.3f} V")


def ramp(room):
    """Rise from clean air to full fire, so the agent sees a trend and not a
    jump. i/steps is simply the fraction of the way through."""
    steps = int(RAMP_S / RAMP_STEP_S)
    print(f"ramp over {RAMP_S:.0f}s in {steps} steps of {RAMP_STEP_S:.0f}s; "
          f"crosses the 1.0 V threshold at about "
          f"{RAMP_S * (1.0 - CLEAN_AIR_V) / (FIRE_V - CLEAN_AIR_V):.0f}s")
    for i in range(steps + 1):
        set_smoke(room, CLEAN_AIR_V + (FIRE_V - CLEAN_AIR_V) * (i / steps))
        time.sleep(RAMP_STEP_S)


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1

    command = argv[0].lower()

    # Default to a room with REAL actuators, not simply the first name
    # alphabetically. Sorted order puts A108 first, and setting the virtual
    # room alight produces no sound at all — which looks exactly like a bug.
    default_room = next((r for r in rooms.names() if rooms.is_real(r, "buzzer")),
                        rooms.names()[0])
    target = argv[1] if len(argv) > 1 else default_room

    targets = rooms.names() if target.lower() == "all" else [target]
    for room in targets:
        if room not in rooms.ROOMS:
            print(f"unknown room {room!r} (known: {rooms.names()})")
            return 1

    for room in targets:
        if command == "on":
            set_smoke(room, FIRE_V)
        elif command == "off":
            set_smoke(room, CLEAN_AIR_V)
        elif command == "ramp":
            ramp(room)
        else:
            try:
                set_smoke(room, float(command))
            except ValueError:
                print(__doc__)
                return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.RequestException as exc:
        print(f"BuildSim unreachable at {twin.BUILDSIM}: {exc}")
        print("Is BuildSim running, and has an agent registered the rooms?")
        sys.exit(1)
