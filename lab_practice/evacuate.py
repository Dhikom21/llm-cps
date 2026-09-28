"""
evacuate.py — the building's response to a fire, across all configured rooms.

The agent handles the PHYSICAL response (heater off, real buzzer). This handles
the BUILDING response, which exists only in the twin:

    smoke rises in a room  ->  find the nearest usable fire exit
                           ->  draw the escape route
                           ->  walk that room's occupants along it
                           ->  raise a critical alert
    smoke clears           ->  put everything back

It never talks to the agent. Both watch the same smoke values and each does its
own job — which is how a real building is arranged, and it means neither can
hang waiting for the other.

--------------------------------------------------------------------------
Why the route is worth more than a line on a screen
--------------------------------------------------------------------------
BuildSim excludes locked, jammed or blocked doors from walkable routes. So if a
door on the short path is locked, the computed route genuinely changes and the
people walk somewhere else. An agent that locks a door is therefore doing
something with a visible, measurable consequence — a far stronger cross-layer
result than an alarm that merely beeps. Try it mid-fire:

    curl -X PUT http://localhost:9090/api/doors -H 'Content-Type: application/json' \
      -d '[{"id":"exit-A152","name":"East fire exit","kind":"fire_exit",
            "level":"level0","room":"A152","state":"blocked","lock_state":"locked"}]'

--------------------------------------------------------------------------
Run (alongside an agent)
--------------------------------------------------------------------------
    export BUILDSIM_URL=http://localhost:9090
    python3 evacuate.py
"""
import os
import signal
import time

import requests

import rooms
import twin

# Rooms treated as building exits. A152 sits at the east entrance (close to
# A109); 1542 is at the far west end. Two genuinely different options, so
# "nearest" means something and blocking one visibly changes the answer.
EXIT_ROOMS = [r.strip() for r in
              os.environ.get("EXIT_ROOMS", "A152,1542").split(",") if r.strip()]

POLL_S       = float(os.environ.get("POLL_S", "2.0"))
STEP_MS      = 700              # time to walk one waypoint
HTTP_TIMEOUT = 3.0

# Where each room's occupants stand before anything happens. Taken from the
# level0 walkable graph so the figures sit inside the room rather than at some
# arbitrary point.
ROOM_XY = {"A109": (278.72, 261.33), "A108": (292.0, 268.0)}

# Who is where at rest.
OCCUPANTS = {
    "A109": [{"id": "p1", "name": "Alice", "icon": "woman"},
             {"id": "p2", "name": "Bob",   "icon": "man"}],
    "A108": [{"id": "p3", "name": "Cleo",  "icon": "woman"}],
}


def get(path, **params):
    r = requests.get(f"{twin.BUILDSIM}{path}", params=params or None,
                     timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def put(path, payload):
    r = requests.put(f"{twin.BUILDSIM}{path}", json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r


def people(room):
    return OCCUPANTS.get(room, [])


def xy(room):
    return ROOM_XY.get(room, (278.72, 261.33))


# ---------------- setup ----------------
def register_exits():
    """Mark the exit rooms as fire exits so the viewer draws them.

    PUT /api/doors replaces the whole collection, so this owns every door in
    the snapshot — anything else you add must go in this same list.
    """
    doors = [{"id": f"exit-{room}", "name": f"Fire exit {room}",
              "kind": "fire_exit", "level": "level0", "room": room,
              "state": "open", "lock_state": "unlocked"}
             for room in EXIT_ROOMS]
    try:
        put("/api/doors", doors)
    except requests.RequestException as e:
        print(f"[warn] could not register exits: {e}")


# ---------------- twin state ----------------
def show_resting():
    """Everyone in their own room, standing still.

    Without this the building is empty until a fire starts, which makes the
    evacuation look like people materialising rather than people leaving.
    """
    occ, ents = {}, []
    for room in rooms.names():
        crowd = people(room)
        if not crowd:
            continue
        occ[f"{rooms.level(room)}/{room}"] = {"persons": crowd, "aliens": []}
        cx, cy = xy(room)
        for i, p in enumerate(crowd):
            ents.append({"id": p["id"], "name": p["name"],
                         "type": "woman" if p["icon"] == "woman" else "man",
                         "level": rooms.level(room),
                         "position": [cx + (i - 0.5) * 4.0, cy + (i % 2) * 4.0],
                         "status": f"in {room}", "transition_ms": 800})
    try:
        put("/api/occupancy", occ)
        put("/api/entities", ents)
    except requests.RequestException as e:
        print(f"[warn] could not place occupants: {e}")


def nearest_exit(room):
    """Ask BuildSim for a walkable route to each exit; keep the shortest.

    Routing is BuildSim's job, not ours — and because it applies the current
    door state, a locked door simply yields no route and that exit drops out of
    the running by itself.
    """
    best = None
    for exit_room in EXIT_ROOMS:
        try:
            res = get("/api/graph/route", from_name=room, to_name=exit_room,
                      level=rooms.level(room), type="walkable")
        except requests.RequestException:
            print(f"  [route] {exit_room}: unreachable (door blocked?)")
            continue
        path = res.get("path") or []
        if not path:
            print(f"  [route] {exit_room}: no path")
            continue
        dist = res.get("distance")
        if dist is None:
            dist = float(len(path))
        print(f"  [route] {exit_room}: {len(path)} waypoints, distance {dist:.1f}")
        if best is None or dist < best[1]:
            best = (exit_room, dist, path)
    return best


def push_route(path, distance):
    """Session overlays are per-browser, so send to every open viewer."""
    try:
        sessions = get("/api/sessions")
    except requests.RequestException:
        return
    if isinstance(sessions, dict):
        sessions = sessions.get("sessions", [])
    for s in sessions or []:
        sid = s.get("id") if isinstance(s, dict) else s
        if sid:
            try:
                put(f"/api/sessions/{sid}/route",
                    {"path": path, "distance": distance})
            except requests.RequestException:
                pass


def walk(room, path):
    """Move that room's figures along the waypoints, one step at a time.

    Each person is placed a couple of waypoints behind the one in front so they
    file out rather than moving as one blob. BuildSim interpolates between the
    old and new positions over transition_ms, so a coarse update rate still
    reads as walking.

    Only this room's occupants move; anyone elsewhere stays put, which is what
    makes a two-room evacuation visibly different from a one-room one.
    """
    crowd = people(room)
    if not crowd:
        return
    lag = 2
    others = [r for r in rooms.names() if r != room]

    for step in range(len(path) + lag * len(crowd)):
        ents = []
        # the people who are leaving
        for i, p in enumerate(crowd):
            idx = max(0, step - i * lag)
            if idx >= len(path):
                continue                        # already out
            wp = path[idx]
            ents.append({"id": p["id"], "name": p["name"],
                         "type": "woman" if p["icon"] == "woman" else "man",
                         "level": wp.get("level", rooms.level(room)),
                         "position": [wp["x"], wp["y"]],
                         "status": "evacuating", "transition_ms": STEP_MS})
        # everyone else carries on as normal
        for other in others:
            cx, cy = xy(other)
            for i, p in enumerate(people(other)):
                ents.append({"id": p["id"], "name": p["name"],
                             "type": "woman" if p["icon"] == "woman" else "man",
                             "level": rooms.level(other),
                             "position": [cx + (i - 0.5) * 4.0, cy],
                             "status": f"in {other}", "transition_ms": STEP_MS})
        try:
            put("/api/entities", ents)
        except requests.RequestException:
            return
        time.sleep(STEP_MS / 1000.0)


def show_alerts(active):
    """One card per burning room. /api/alerts replaces the whole collection,
    so every current alert is rebuilt on each change."""
    cards = []
    for room, exit_room in sorted(active.items()):
        if exit_room:
            cards.append({"id": f"fire-{room}", "severity": "critical",
                          "title": f"Fire confirmed in {room}",
                          "message": f"Evacuating {len(people(room))} occupants "
                                     f"via {exit_room}.",
                          "level": rooms.level(room), "room": room})
        else:
            cards.append({"id": f"fire-{room}", "severity": "critical",
                          "title": f"Fire in {room} — NO ROUTE OUT",
                          "message": "All fire exits are locked or blocked.",
                          "level": rooms.level(room), "room": room})
    try:
        put("/api/alerts", cards)
    except requests.RequestException:
        pass


def clear_all():
    for path, payload in (("/api/alerts", []), ("/api/entities", []),
                          ("/api/occupancy", {})):
        try:
            put(path, payload)
        except requests.RequestException:
            pass
    push_route([], 0)


# ---------------- main ----------------
running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

register_exits()
show_resting()
print(f"evacuation service watching {rooms.names()}: "
      f"threshold {rooms.SMOKE_THRESHOLD} V, exits {EXIT_ROOMS}. Ctrl-C to stop.")

active = {}          # room -> exit used (or None if no route)

try:
    while running:
        for room in rooms.names():
            smoke = twin.read_smoke(room)
            burning = smoke >= rooms.SMOKE_THRESHOLD

            if burning and room not in active:
                print(f"\n[FIRE] {room}: smoke {smoke:.2f} V — planning evacuation")
                best = nearest_exit(room)
                if best is None:
                    print(f"[FIRE] {room}: no usable exit")
                    active[room] = None
                    show_alerts(active)
                else:
                    exit_room, dist, path = best
                    active[room] = exit_room
                    print(f"[FIRE] {room}: nearest exit {exit_room} "
                          f"(distance {dist:.1f})")
                    show_alerts(active)
                    push_route(path, dist)
                    walk(room, path)
                    print(f"[FIRE] {room}: occupants out")

            elif not burning and room in active:
                print(f"[CLEAR] {room}: smoke gone — occupants return")
                del active[room]
                show_alerts(active)
                if not active:
                    push_route([], 0)
                show_resting()

        time.sleep(POLL_S)
finally:
    clear_all()
    print("\nstopped — twin cleared")
