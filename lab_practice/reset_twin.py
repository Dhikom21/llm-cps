"""
reset_twin.py — put BuildSim back to a clean state between experiment runs.

BuildSim keeps everything in memory and nothing expires. So a process that was
killed rather than stopped cleanly leaves its fire effects, its occupants and
its alert cards behind, and equipment registered by an earlier version of the
project stays on the floor plan forever. The view then shows a mixture of
several runs, which is confusing to look at and worse to photograph for a
report.

This clears the transient state and, with --equipment, removes equipment that
the current rooms.py does not account for.

    python3 reset_twin.py                 # effects, entities, alerts, routes
    python3 reset_twin.py --equipment     # also delete unrecognised equipment
    python3 reset_twin.py --history       # also wipe the readings database
    python3 reset_twin.py --all           # all of the above
    python3 reset_twin.py --list          # show what is registered, change nothing

--history matters more than it sounds. The twin holds only current values, but
history.py keeps a rolling record — so clearing the twin while leaving the
database behind means the next run's agent is handed a `recent` window full of
the PREVIOUS run's readings. That happened: a finished fire's 3.0 V rows sat
alongside a current 0.10 V, and the model duly reported smoke above threshold.
It looked like a hallucination and was stale data.

Nothing here touches the Pi. Restarting an agent re-registers its rooms within
one cycle, so a reset costs you a couple of seconds of floor plan, not data —
the readings live in history.py and the decisions in the audit log, neither of
which this can affect.
"""
import sys

import requests

import rooms
import twin

TIMEOUT = 3.0

# Everything an agent or service of ours legitimately owns, derived from the
# current configuration. Anything else on the floor plan is from an older run.
def expected_equipment():
    ids = set()
    for room in rooms.names():
        ids.update({twin.temp_eq(room), twin.heat_eq(room),
                    twin.alarm_eq(room), twin.smoke_eq(room)})
    return ids


def list_equipment():
    try:
        r = requests.get(f"{twin.BUILDSIM}/api/equipment", timeout=TIMEOUT)
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, list) else body.get("equipment", [])
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"could not reach BuildSim at {twin.BUILDSIM}: {exc}")
        return None


def clear_transient():
    """The four collections that accumulate across runs.

    Each is a replace-the-whole-thing endpoint, so an empty list or object
    clears it outright. Sessions get their route cleared separately because
    overlays are per-browser rather than global.
    """
    for path, payload, label in (
            ("/api/effects", [], "fire and smoke effects"),
            ("/api/entities", [], "moving figures"),
            ("/api/alerts", [], "alert cards"),
            ("/api/occupancy", {}, "room occupancy"),
            ("/api/doors", [], "doors and fire exits")):
        try:
            requests.put(f"{twin.BUILDSIM}{path}", json=payload,
                         timeout=TIMEOUT).raise_for_status()
            print(f"  cleared {label}")
        except requests.RequestException as exc:
            print(f"  could not clear {label}: {exc}")

    try:
        sessions = requests.get(f"{twin.BUILDSIM}/api/sessions",
                                timeout=TIMEOUT).json()
        if isinstance(sessions, dict):
            sessions = sessions.get("sessions", [])
        for s in sessions or []:
            sid = s.get("id") if isinstance(s, dict) else s
            if sid:
                requests.put(f"{twin.BUILDSIM}/api/sessions/{sid}/route",
                             json={"path": [], "distance": 0}, timeout=TIMEOUT)
        print(f"  cleared escape routes in {len(sessions or [])} open viewer(s)")
    except (requests.RequestException, ValueError, TypeError):
        pass


def reset_smoke():
    """Put every configured room back to clean air.

    Done explicitly rather than left to inject_fire, because a run abandoned
    mid-fire leaves the sensor high — and the next agent to start would
    immediately sound the alarm for a fire that finished yesterday.
    """
    for room in rooms.names():
        try:
            twin.set_smoke(room, 0.10)
            print(f"  {room}: smoke -> 0.100 V")
        except requests.RequestException:
            print(f"  {room}: no smoke sensor registered yet (harmless)")


def delete_unexpected():
    items = list_equipment()
    if items is None:
        return
    keep = expected_equipment()
    removed = 0
    for item in items:
        eq_id = item.get("id")
        if not eq_id or eq_id in keep:
            continue
        try:
            requests.delete(f"{twin.BUILDSIM}/api/equipment/{eq_id}",
                            timeout=TIMEOUT).raise_for_status()
            print(f"  deleted {eq_id}  ({item.get('room', '?')})")
            removed += 1
        except requests.RequestException as exc:
            print(f"  could not delete {eq_id}: {exc}")
    print(f"  {removed} unrecognised item(s) removed, {len(keep)} kept")


def clear_history():
    """Empty the readings table.

    Separate from the twin reset because they are different kinds of state:
    the twin holds what is true now, history holds what was true. Clearing one
    without the other is what produced the stale-window problem above.

    The audit log is deliberately NOT touched — it is the experimental record,
    and nothing in this file should be able to destroy evidence.
    """
    import history
    try:
        with history._lock:
            history._db().execute("DELETE FROM readings")
            history._db().commit()
        print(f"  wiped readings in {history.DB_PATH}")
        print("  (decisions.jsonl left alone — that is the experimental record)")
    except Exception as exc:
        print(f"  could not wipe history: {exc}")


def main():
    args = sys.argv[1:]
    if "--all" in args:
        args += ["--equipment", "--history"]

    if "--list" in args:
        items = list_equipment()
        if items is None:
            return 1
        keep = expected_equipment()
        print(f"\n{len(items)} pieces of equipment registered at {twin.BUILDSIM}:\n")
        for item in sorted(items, key=lambda i: str(i.get("id"))):
            mark = "current" if item.get("id") in keep else "STALE  "
            print(f"  [{mark}] {item.get('id'):<28} "
                  f"{item.get('room', '?'):<8} {item.get('type', '')}")
        return 0

    print(f"resetting {twin.BUILDSIM}")
    clear_transient()
    reset_smoke()
    if "--equipment" in args:
        delete_unexpected()
    else:
        print("  (equipment left alone — pass --equipment to remove stale items)")

    if "--history" in args:
        clear_history()
    else:
        print("  (readings left alone — pass --history to wipe them)")

    try:
        requests.post(f"{twin.BUILDSIM}/api/equipment/notify", timeout=TIMEOUT)
    except requests.RequestException:
        pass

    print("\ndone. Restart the agent and evacuate.py so they re-register.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
