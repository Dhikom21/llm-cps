"""
level5_multi_room_agent.py -- a rule-based agent that watches THREE rooms and
turns each room's heater on/off to hold the comfort band. Think of it as one
thermostat agent per room, running together (a simple multi-agent controller).

It pairs with level4_multi_room_sensor.py (which provides the rooms + heaters)
and the heatmap (which will visibly follow each heater's effect).

Loop, per room:
    perceive  -> read recent avg temperature from TimescaleDB
    reason    -> below band = heater ON, above band = OFF, inside = keep
    act       -> PUT the heater state (only when it changes)

Prerequisites:
    BuildSim, docker compose (TimescaleDB + Mosquitto),
    python level4_consumer.py
    python level4_multi_room_sensor.py     # rooms + heaters + temp feed
    python heatmap_buildsim.py             # (optional) watch the colours follow

Run:
    python level5_multi_room_agent.py

Note: let the AGENT own the heaters -- don't also curl them or run the level7
highlight tool at the same time.
"""
import signal
import sys
import time

import requests

import db   # reused read-only

BASE         = "http://localhost:9090"
ROOMS        = ["A109", "A110", "A111"]
HEATER_FMT   = "sim-{room}-heater-state"   # matches level4_multi_room_sensor.py

COMFORT_LOW  = 20.0
COMFORT_HIGH = 23.0
PERIOD_S     = 3.0

PERCEIVE_SQL = """
    SELECT value FROM readings
    WHERE room = %s AND type = 'temperature'
          AND ts > now() - INTERVAL '10 seconds'
    ORDER BY ts ASC
"""

conn = db.connect()
last_command = {room: None for room in ROOMS}   # remember per-room command


def perceive(room):
    with conn.cursor() as cur:
        cur.execute(PERCEIVE_SQL, [room])
        rows = cur.fetchall()
    if not rows:
        return None
    values = [r[0] for r in rows]
    return sum(values) / len(values)


def reason(avg):
    if avg is None:
        return None                # no data -> keep
    if avg < COMFORT_LOW:
        return "on"
    if avg > COMFORT_HIGH:
        return "off"
    return None                    # inside band -> keep


def act(room, decision):
    if decision is None or decision == last_command[room]:
        return
    heater = HEATER_FMT.format(room=room)
    try:
        requests.put(f"{BASE}/api/actuators/{heater}/state",
                     json={"state": decision}, timeout=2.0)
        last_command[room] = decision
        print(f"    {room}: heater -> {decision}")
    except requests.RequestException as e:
        print(f"    {room}: command failed: {e}")


def cleanup(*_):
    print("\nstopping multi-room agent.")
    try: conn.close()
    except Exception: pass
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

print(f"multi-room agent running. rooms={ROOMS}, band {COMFORT_LOW}-{COMFORT_HIGH} C, "
      f"cycle {PERIOD_S}s. Ctrl-C to stop.")

while True:
    stamp = time.strftime('%H:%M:%S')
    line = []
    for room in ROOMS:
        avg = perceive(room)
        decision = reason(avg)
        act(room, decision)
        shown = f"{avg:.1f}" if avg is not None else "--"
        band = "OK" if (avg is not None and COMFORT_LOW <= avg <= COMFORT_HIGH) else "!!"
        line.append(f"{room}={shown}({band})")
    print(f"[{stamp}] " + "  ".join(line))
    time.sleep(PERIOD_S)
