"""
baseline_controller.py -- Phase 1 (part 2 of 2): the "dumb" baseline.

A FIXED-SCHEDULE heater controller. It is deliberately naive:

  * During scheduled "occupied" hours  -> keep the comfort band (20-23 C).
  * Outside those hours (setback)       -> heater OFF.
  * It NEVER checks whether the room is actually occupied.

That last point is the whole point: it heats every scheduled hour even if the
room is empty. A smart, occupancy-aware controller (later phases) saves energy
by NOT doing that. This baseline is the yardstick you must beat.

Measure its energy by running the meter alongside it:
    CONTROLLER=baseline python energy_tracker.py

Prerequisites (same stack as level 5):
    BuildSim, docker compose (Mosquitto + TimescaleDB),
    python level4_consumer.py, python level4_sensor.py   # provide temp + heater

Run:
    python baseline_controller.py
    # test it immediately, ignoring the clock:
    OCCUPIED=always python baseline_controller.py

It only commands the heater; energy is measured by energy_tracker.py.
"""
import os
import signal
import sys
import time
from datetime import datetime

import requests

import db   # reused read-only

BASE         = "http://localhost:9090"
ROOM         = "A109"
ACT_STATE    = "level4-heater-A109-state"

COMFORT_LOW  = 20.0
COMFORT_HIGH = 23.0

WORK_START   = 8      # occupied from 08:00 ...
WORK_END     = 18     # ... until 18:00 (local time)
PERIOD_S     = 3.0

# OCCUPIED env override for testing: "always" | "never" | "" (=use the clock)
OCCUPIED_OVERRIDE = os.environ.get("OCCUPIED", "").strip().lower()

PERCEIVE_SQL = """
    SELECT value FROM readings
    WHERE room = %s AND type = 'temperature'
          AND ts > now() - INTERVAL '10 seconds'
    ORDER BY ts ASC
"""

conn = db.connect()
last_command = None


def is_occupied() -> bool:
    """Fixed schedule only -- no real occupancy sensing (that's the point)."""
    if OCCUPIED_OVERRIDE == "always":
        return True
    if OCCUPIED_OVERRIDE == "never":
        return False
    hour = datetime.now().hour
    return WORK_START <= hour < WORK_END


def perceive():
    with conn.cursor() as cur:
        cur.execute(PERCEIVE_SQL, [ROOM])
        rows = cur.fetchall()
    if not rows:
        return None
    values = [r[0] for r in rows]
    return sum(values) / len(values)


def decide(avg, occupied):
    """Fixed-schedule thermostat: hold the band when occupied, else OFF."""
    if not occupied:
        return "off"                 # setback -- no heating out of hours
    if avg is None:
        return None                  # no data -> keep current
    if avg < COMFORT_LOW:
        return "on"
    if avg > COMFORT_HIGH:
        return "off"
    return None                      # inside band -> keep current


def act(decision):
    global last_command
    if decision is None or decision == last_command:
        return
    try:
        requests.put(f"{BASE}/api/actuators/{ACT_STATE}/state",
                     json={"state": decision}, timeout=2.0)
        last_command = decision
        print(f"  -> heater {decision}")
    except requests.RequestException as e:
        print(f"  ! actuator command failed: {e}")


def cleanup(*_):
    print("\nstopping baseline controller.")
    try: conn.close()
    except Exception: pass
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

mode = OCCUPIED_OVERRIDE or f"clock {WORK_START:02d}:00-{WORK_END:02d}:00"
print(f"baseline (fixed-schedule) controller running. occupancy={mode}, "
      f"band {COMFORT_LOW}-{COMFORT_HIGH} C, cycle {PERIOD_S}s. Ctrl-C to stop.")

while True:
    occ = is_occupied()
    avg = perceive()
    decision = decide(avg, occ)
    stamp = time.strftime('%H:%M:%S')
    if avg is None:
        print(f"[{stamp}] occupied={occ}  (no temp data)  decision={decision or 'keep'}")
    else:
        print(f"[{stamp}] occupied={occ}  avg={avg:5.2f}  decision={decision or 'keep'}")
    act(decision)
    time.sleep(PERIOD_S)
