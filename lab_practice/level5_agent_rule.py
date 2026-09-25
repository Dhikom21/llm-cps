"""
Level 5 — Rule-based agent (TimescaleDB version).

Closes the loop:
    sensor (level4) -> MQTT -> consumer -> TimescaleDB -> AGENT
                                                            |
                                                            v
                                                  BuildSim actuator -> sim

Pre-requisites running:
    BuildSim, Mosquitto, TimescaleDB,
    level4_consumer.py, level4_sensor.py

Then:
    python level5_agent_rule.py

Watch the temperature in A109 oscillate inside the comfort band as the
agent toggles the heater.
"""
import time
import signal
import sys

import requests

import db

BASE = "http://localhost:9090"

ROOM      = "A109"
ACT_STATE = "level4-heater-A109-state"

# Comfort band
COMFORT_LOW  = 20.0
COMFORT_HIGH = 23.0

# Decision frequency. SLOWER than the sensor rate — that's intentional.
# Real agents act on aggregates, not individual readings.
PERIOD_S = 3.0

PERCEIVE_SQL = """
    SELECT value FROM readings
    WHERE room = %s AND type = 'temperature'
          AND ts > now() - INTERVAL '10 seconds'
    ORDER BY ts ASC
"""

conn = db.connect()
last_command = None

def perceive():
    with conn.cursor() as cur:
        cur.execute(PERCEIVE_SQL, [ROOM])
        rows = cur.fetchall()
    if not rows:
        return None, None
    values = [r[0] for r in rows]
    avg = sum(values) / len(values)
    trend = values[-1] - values[0] if len(values) >= 2 else 0.0
    return avg, trend

def reason(avg, trend):
    """Pure function. Returns 'on', 'off', or None (= keep current)."""
    if avg is None:
        return None
    if avg < COMFORT_LOW:
        return "on"
    if avg > COMFORT_HIGH:
        return "off"
    # Inside band — keep whatever we last sent
    return None

def act(decision):
    global last_command
    if decision is None or decision == last_command:
        return
    try:
        requests.put(
            f"{BASE}/api/actuators/{ACT_STATE}/state",
            json={"state": decision},
            timeout=2.0,
        )
        last_command = decision
        print(f"  -> commanded heater {decision}")
    except requests.RequestException as e:
        print(f"  ! actuator command failed: {e}")

def cleanup(*_):
    print("\nstopping agent.")
    try: conn.close()
    except Exception: pass
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

print(f"agent running. comfort band {COMFORT_LOW}–{COMFORT_HIGH} C, "
      f"cycle {PERIOD_S}s. Ctrl-C to stop.")

while True:
    avg, trend = perceive()
    decision = reason(avg, trend)
    if avg is None:
        print(f"[{time.strftime('%H:%M:%S')}] no recent data for {ROOM}")
    else:
        marker = "OK" if COMFORT_LOW <= avg <= COMFORT_HIGH else "!!"
        print(f"[{time.strftime('%H:%M:%S')}] avg={avg:5.2f}  trend={trend:+.2f}"
              f"  band={marker}  decision={decision or 'keep'}")
    act(decision)
    time.sleep(PERIOD_S)
