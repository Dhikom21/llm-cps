"""
evacuate.py — Scenario B's response layer: people, routes and alerts.

edge_agent.py handles the PHYSICAL response to smoke (heater off, real buzzer).
This handles the BUILDING response, which exists only in the twin:

    smoke rises  ->  put people in A109
                 ->  find the nearest usable fire exit
                 ->  draw the escape route in the viewer
                 ->  walk the people along it
                 ->  raise a critical alert
    smoke clears ->  take all of that away again

The two run side by side and never talk to each other. Both watch the same
smoke reading and each does its own job — which is what a real building does,
and it means neither can hang waiting for the other.

--------------------------------------------------------------------------
Why the route is worth more than a line on a screen
--------------------------------------------------------------------------
BuildSim excludes a locked, jammed or blocked door from walkable routes. So if
a door on the short path is locked, the computed route genuinely changes and
the people walk somewhere else. An agent that locks a door is therefore doing
something with visible consequences — which is exactly the cross-layer effect
the project is trying to measure. Try it:

    curl -X PUT http://localhost:9090/api/doors -H 'Content-Type: application/json' \
      -d '[{"id":"exit-A152","name":"East fire exit","kind":"fire_exit",
            "level":"level0","room":"A152","state":"blocked","lock_state":"locked"}]'

...and watch the evacuation re-route to the far exit on the next cycle.

--------------------------------------------------------------------------
Run (on the Pi, alongside edge_agent.py)
--------------------------------------------------------------------------
    export BUILDSIM_URL=http://localhost:9090
    python3 evacuate.py

Then, from anywhere:
    python3 inject_fire.py ramp
"""
import os
import signal
import time

import requests

BUILDSIM     = os.environ.get("BUILDSIM_URL", "http://localhost:9090")
SMOKE_VAL_ID = "pi-smoke-A109-val"
ROOM, LEVEL  = "A109", "level0"

# A109's entry node on level0's walkable graph — where the occupants stand
# before anything happens. Taken from the floor-plan data, so the figures sit
# inside the room rather than at an arbitrary point.
ROOM_XY = (278.72, 261.33)

# Rooms treated as building exits. A152 is the east entrance (close to A109);
# 1542 is at the far west end. Two genuinely different options, so "nearest"
# means something and blocking one visibly changes the answer.
EXIT_ROOMS = [r.strip() for r in
              os.environ.get("EXIT_ROOMS", "A152,1542").split(",") if r.strip()]

SMOKE_THRESHOLD = float(os.environ.get("SMOKE_THRESHOLD", "1.0"))
POLL_S          = float(os.environ.get("POLL_S", "2.0"))
STEP_MS         = 700           # time to walk one waypoint
HTTP_TIMEOUT    = 3.0

# Who is in the room when the fire starts.
PEOPLE = [
    {"id": "person-1", "name": "Alice", "icon": "woman"},
    {"id": "person-2", "name": "Bob",   "icon": "man"},
    {"id": "person-3", "name": "Cleo",  "icon": "woman"},
]


# ---------------- small REST helpers ----------------
def get(path, **params):
    r = requests.get(f"{BUILDSIM}{path}", params=params or None, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def put(path, payload):
    r = requests.put(f"{BUILDSIM}{path}", json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r


# ---------------- perceive ----------------
def read_smoke():
    """The same value edge_agent acts on — one source of truth, two consumers."""
    try:
        return float(get(f"/api/sensors/{SMOKE_VAL_ID}").get("value", 0.0))
    except (requests.RequestException, ValueError, TypeError):
        return 0.0


# ---------------- plan ----------------
def register_exits():
    """Mark the exit rooms as fire exits so the viewer draws them.

    NOTE: PUT /api/doors replaces the whole collection, so this owns every
    door in the snapshot. Anything else you add must go in this same list.
    """
    doors = [{"id": f"exit-{room}", "name": f"Fire exit {room}",
              "kind": "fire_exit", "level": LEVEL, "room": room,
              "state": "open", "lock_state": "unlocked"}
             for room in EXIT_ROOMS]
    try:
        put("/api/doors", doors)
    except requests.RequestException as e:
        print(f"[warn] could not register exits: {e}")


def nearest_exit():
    """Ask BuildSim for a walkable route to each exit and keep the shortest.

    Routing is BuildSim's job, not ours — and because it applies the current
    door state, a locked door simply produces no route and that exit drops out
    of the running automatically.
    """
    best = None
    for room in EXIT_ROOMS:
        try:
            res = get("/api/graph/route", from_name=ROOM, to_name=room,
                      level=LEVEL, type="walkable")
        except requests.RequestException:
            print(f"[route] {room}: unreachable (door blocked?)")
            continue

        path = res.get("path") or []
        if not path:
            print(f"[route] {room}: no path")
            continue

        dist = res.get("distance")
        if dist is None:
            dist = float(len(path))          # fall back to hop count
        print(f"[route] {room}: {len(path)} waypoints, distance {dist:.1f}")

        if best is None or dist < best[1]:
            best = (room, dist, path)

    return best


# ---------------- act (in the twin) ----------------
def show_alert(exit_room):
    put("/api/alerts", [{
        "id": "fire-A109", "severity": "critical",
        "title": f"Fire confirmed in {ROOM}",
        "message": f"Evacuating {len(PEOPLE)} occupants via {exit_room}.",
        "level": LEVEL, "room": ROOM}])


def show_occupancy(room):
    put("/api/occupancy", {f"{LEVEL}/{room}": {"persons": PEOPLE, "aliens": []}})


def show_resting():
    """Put the occupants in A109, standing still, going about their day.

    Called at startup and again after the smoke clears. Without this the room
    is empty until a fire begins, which makes the evacuation look like people
    materialising rather than people leaving.
    """
    try:
        show_occupancy(ROOM)
        put("/api/entities", [
            {"id": p["id"], "name": p["name"],
             "type": "woman" if p["icon"] == "woman" else "man",
             "level": LEVEL,
             "position": [ROOM_XY[0] + (i - 1) * 4.0, ROOM_XY[1] + (i % 2) * 4.0],
             "status": "in room", "transition_ms": 800}
            for i, p in enumerate(PEOPLE)])
    except requests.RequestException as e:
        print(f"[warn] could not place occupants: {e}")


def push_route_to_viewers(path, distance):
    """Session overlays are per-browser, so send the route to every open viewer."""
    try:
        sessions = get("/api/sessions")
    except requests.RequestException:
        return
    if isinstance(sessions, dict):
        sessions = sessions.get("sessions", [])
    for s in sessions or []:
        sid = s.get("id") if isinstance(s, dict) else s
        if not sid:
            continue
        try:
            put(f"/api/sessions/{sid}/route", {"path": path, "distance": distance})
        except requests.RequestException:
            pass


def walk(path):
    """Move the figures along the waypoints, a step at a time.

    Each person is placed a couple of waypoints behind the one in front, so
    they file out rather than moving as one blob. BuildSim interpolates between
    the old and new positions over transition_ms, so a coarse update rate still
    looks like walking.
    """
    lag = 2
    steps = len(path) + lag * len(PEOPLE)

    for step in range(steps):
        entities = []
        for i, person in enumerate(PEOPLE):
            idx = step - i * lag
            if idx < 0:
                idx = 0                      # not moving yet
            if idx >= len(path):
                continue                     # already out of the building
            wp = path[idx]
            entities.append({
                "id": person["id"], "name": person["name"],
                "type": "woman" if person["icon"] == "woman" else "man",
                "level": wp.get("level", LEVEL),
                "position": [wp["x"], wp["y"]],
                "status": "evacuating",
                "transition_ms": STEP_MS})

        try:
            put("/api/entities", entities)
        except requests.RequestException:
            return
        if not entities:
            return                           # everyone is out
        time.sleep(STEP_MS / 1000.0)


def clear_all():
    """Put the twin back the way we found it."""
    for path, payload in (("/api/alerts", []),
                          ("/api/entities", []),
                          ("/api/occupancy", {})):
        try:
            put(path, payload)
        except requests.RequestException:
            pass
    try:
        sessions = get("/api/sessions")
        if isinstance(sessions, dict):
            sessions = sessions.get("sessions", [])
        for s in sessions or []:
            sid = s.get("id") if isinstance(s, dict) else s
            if sid:
                put(f"/api/sessions/{sid}/route", {"path": [], "distance": 0})
    except requests.RequestException:
        pass


# ---------------- main ----------------
running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

register_exits()
show_resting()
print(f"evacuation service watching {ROOM}: threshold {SMOKE_THRESHOLD} V, "
      f"exits {EXIT_ROOMS}, {len(PEOPLE)} occupants. Ctrl-C to stop.")

evacuating = False

try:
    while running:
        smoke = read_smoke()

        if smoke >= SMOKE_THRESHOLD and not evacuating:
            evacuating = True
            print(f"\n[FIRE] smoke {smoke:.2f} V — planning evacuation")

            show_occupancy(ROOM)
            best = nearest_exit()

            if best is None:
                print("[FIRE] no usable exit — every route is blocked")
                put("/api/alerts", [{
                    "id": "fire-A109", "severity": "critical",
                    "title": f"Fire in {ROOM} — NO ROUTE OUT",
                    "message": "All fire exits are locked or blocked.",
                    "level": LEVEL, "room": ROOM}])
            else:
                exit_room, dist, path = best
                print(f"[FIRE] nearest exit {exit_room} "
                      f"({len(path)} waypoints, distance {dist:.1f})")
                show_alert(exit_room)
                push_route_to_viewers(path, dist)
                walk(path)
                show_occupancy(exit_room)      # they are now at the exit
                print("[FIRE] occupants out")

        elif smoke < SMOKE_THRESHOLD and evacuating:
            evacuating = False
            print("[CLEAR] smoke gone — occupants return to the room")
            clear_all()
            show_resting()        # back to normal, not to an empty building

        time.sleep(POLL_S)
finally:
    clear_all()
    print("\nstopped — twin cleared")
