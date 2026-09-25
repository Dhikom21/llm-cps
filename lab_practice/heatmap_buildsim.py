"""
heatmap_buildsim.py -- Dashboard piece 2: colour rooms in the BuildSim 3D
viewer by their live temperature (a "temperature heatmap" on the building).

How it works (same pieces you already know):
  * read the latest temperature PER ROOM from TimescaleDB
  * map each temperature to a colour  (cold = blue ... hot = red)
  * PUT those colours as "highlights" onto the active BuildSim browser session
    (the same /highlights API used in level1_curl.sh and level7's highlight tool)

Only rooms that have recent temperature data get coloured. Right now that's
mainly A109 (the simulator only drives A109); if you stream more rooms
(e.g. map the Kaggle devices to several rooms), they light up automatically.

Prerequisites:
    BuildSim running + a browser tab open at http://localhost:9090
    docker compose up -d           # TimescaleDB
    a temperature source running    # level4_sensor.py  OR  replay_kaggle.py
                                     # + level4_consumer.py

Run:
    python heatmap_buildsim.py
"""
import signal
import sys
import time

import requests

import db   # reused read-only

BASE      = "http://localhost:9090"
LEVEL     = "level0"
POLL_S    = 3.0

OPACITY   = 0.6

# Temperature colour bands (edit thresholds/colours to taste).
# Each entry: (upper_bound_C, hex_colour, label). First band whose bound > temp wins.
BANDS = [
    (18.0,  "#2166ac", "Cold     < 18 C"),
    (20.0,  "#67a9cf", "Cool     18-20 C"),
    (23.0,  "#1a9850", "Comfort  20-23 C"),
    (26.0,  "#fdae61", "Warm     23-26 C"),
    (999.0, "#d73027", "Hot      > 26 C"),
]

LATEST_SQL = """
    SELECT DISTINCT ON (room) room, value
    FROM readings
    WHERE type = 'temperature'
      AND ts > now() - INTERVAL '120 seconds'
    ORDER BY room, ts DESC
"""

conn = db.connect()


def get_room_ids():
    """Map room NAME -> integer room_id (the viewer highlights by id)."""
    r = requests.get(f"{BASE}/api/building/floors/{LEVEL}", timeout=3.0).json()
    return {room["name"]: room["id"] for room in r.get("rooms", [])}


def get_session():
    """Return the most-recently-active browser session id, or None."""
    try:
        s = requests.get(f"{BASE}/api/sessions", timeout=3.0).json()
        if not s:
            return None
        s.sort(key=lambda x: x.get("last_ws_active", ""), reverse=True)
        return s[0]["id"]
    except requests.RequestException:
        return None


def temp_to_color(t):
    """Return the band colour for temperature t (discrete, tunable bands)."""
    for upper, color, _label in BANDS:
        if t < upper:
            return color
    return BANDS[-1][1]


def latest_temps():
    with conn.cursor() as cur:
        cur.execute(LATEST_SQL)
        return {room: val for room, val in cur.fetchall()}


def cleanup(*_):
    print("\nstopping heatmap.")
    try: conn.close()
    except Exception: pass
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

room_ids = get_room_ids()
print(f"heatmap running. {len(room_ids)} rooms known on {LEVEL}. Ctrl-C to stop.")
print("legend:")
for _upper, color, label in BANDS:
    print(f"   {color}  {label}")

while True:
    session = get_session()
    if not session:
        print(f"[{time.strftime('%H:%M:%S')}] no active browser session "
              f"(open http://localhost:9090)")
        time.sleep(POLL_S)
        continue

    temps = latest_temps()
    highlights = []
    for room, val in temps.items():
        rid = room_ids.get(room)
        if rid is None:
            continue
        highlights.append({"room_id": rid,
                           "color": temp_to_color(val),
                           "opacity": OPACITY})

    if highlights:
        try:
            requests.put(f"{BASE}/api/sessions/{session}/highlights",
                         json=highlights, timeout=3.0)
            shown = ", ".join(f"{r}={t:.1f}C" for r, t in temps.items() if r in room_ids)
            print(f"[{time.strftime('%H:%M:%S')}] coloured {len(highlights)} room(s): {shown}")
        except requests.RequestException as e:
            print(f"  ! highlight failed: {e}")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] no recent temperature data to colour")

    time.sleep(POLL_S)
