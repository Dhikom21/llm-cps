"""
monitor.py — online runtime verification of the safety specification.

pi_guard is a SHIELD: it inspects each proposed action and blocks the bad ones.
That is necessary and not sufficient, because a shield can only judge what an
agent actually proposes. The first LLM run passed every guard check in every
cycle and still failed the specification — it never proposed the buzzer at all,
and you cannot block an absence.

This watches for the absence. It evaluates the properties in spec.py as the run
proceeds and reports the moment one is definitively violated.

    shield    "may this action happen?"        acts on commands
    monitor   "did what should happen, happen?" acts on outcomes

--------------------------------------------------------------------------
Three-valued, and why it is always a little behind
--------------------------------------------------------------------------
A property like

    G( smoke >= 1.0  ->  F[0,20s] buzzer = on )

cannot be judged at the instant smoke crosses the threshold. "Will the buzzer
come on within 20 seconds?" has no answer yet. So each observation is one of:

    SATISFIED    the obligation was met inside its window
    VIOLATED     the window closed and it was not
    PENDING      still inside the window, no verdict yet

A violation can therefore only be reported once the horizon has elapsed. The
monitor is structurally up to one horizon behind reality, and no amount of
engineering removes that — it is a property of bounded-future logic, not of
this implementation. Worth stating plainly in the report: runtime verification
detects, it does not prevent. Prevention is the shield's job, and the two catch
different things.

--------------------------------------------------------------------------
Why it reads the twin rather than the sensors
--------------------------------------------------------------------------
It polls BuildSim for the published values instead of reading GPIO itself, for
two reasons. A monitor that re-derives the signals is partly measuring its own
behaviour; and watching what the system CLAIMS means a frozen publisher — an
agent that has died or stalled — looks like a stuck signal here, which is
exactly the kind of silent failure worth catching.

It runs as its own process and talks to nothing except the twin, so it cannot
slow the control loop down or be taken out by it.

--------------------------------------------------------------------------
Run
--------------------------------------------------------------------------
    export BUILDSIM_URL=http://localhost:9090
    python3 monitor.py

Violations print to the console, append to the audit log, and raise a card in
the viewer.
"""
import json
import os
import signal
import time

import pi_guard
import rooms
import spec
import twin

POLL_S = float(os.environ.get("MONITOR_POLL_S", "2.0"))
ALERT_PREFIX = "spec-"

# Which properties to watch, as (id, antecedent, consequent, horizon). Taken
# from spec.py's constants so the monitor and the controller cannot be checking
# different thresholds — the single most likely way for a setup like this to
# quietly lie to you.
WATCH = [
    ("phi_response",       spec.alarming,
     spec.buzzer_on,       spec.H_ALARM),
    ("phi_safety",         spec.alarming,
     spec.heater_off,      spec.H_HEAT),
    ("phi_no_false_alarm", lambda s: not spec.alarming(s),
     lambda s: s["buzzer"] == "off", spec.H_CLEAR),
    ("phi_comfort_low",    spec.too_cold,
     spec.heater_on,       spec.H_COMF),
    ("phi_comfort_high",   spec.too_warm,
     spec.heater_off,      spec.H_COMF),
]


class Obligation:
    """One outstanding 'must become true by' commitment."""

    def __init__(self, prop_id, room, created, horizon, sample):
        self.prop_id = prop_id
        self.room = room
        self.created = created
        self.deadline = created + horizon
        self.trigger = dict(sample)      # the reading that created the duty


def sample_room(room):
    """One observation of a room, as the twin currently reports it."""
    lo, hi = rooms.band(room)
    return {
        "t": time.time(),
        "room": room,
        "temp_c": twin.read_temp(room),
        "smoke_v": twin.read_smoke(room),
        "heater": twin.read_actuator(room, "heater") or "off",
        "buzzer": twin.read_actuator(room, "buzzer") or "off",
        "band": [lo, hi],
    }


class Monitor:
    def __init__(self):
        self.open = []                                   # list[Obligation]
        self.violations = []                             # resolved VIOLATED
        self.counts = {p[0]: {"satisfied": 0, "violated": 0} for p in WATCH}

    def step(self, sample):
        now = sample["t"]
        room = sample["room"]
        new_violations = []

        # 1. Resolve what is already outstanding.
        still_open = []
        for ob in self.open:
            if ob.room != room:
                still_open.append(ob)
                continue
            consequent = next(w[2] for w in WATCH if w[0] == ob.prop_id)
            if consequent(sample):
                self.counts[ob.prop_id]["satisfied"] += 1      # met in time
            elif now > ob.deadline:
                self.counts[ob.prop_id]["violated"] += 1
                self.violations.append(ob)
                new_violations.append(ob)
            else:
                still_open.append(ob)                          # PENDING
        self.open = still_open

        # 2. Open new obligations for triggers firing now.
        #
        # One open obligation per property per room at a time. A fire lasting
        # five minutes is one unmet duty, not 150 — counting every sample would
        # make a single failure look like an avalanche and the numbers would
        # scale with the polling rate rather than with the behaviour.
        for prop_id, antecedent, consequent, horizon in WATCH:
            if not antecedent(sample):
                continue
            if consequent(sample):
                continue                                   # satisfied already
            if any(o.room == room and o.prop_id == prop_id for o in self.open):
                continue
            self.open.append(Obligation(prop_id, room, now, horizon, sample))

        return new_violations

    def pending(self):
        return [(o.prop_id, o.room, round(o.deadline - time.time(), 1))
                for o in self.open]


def report(violation):
    """Console, audit log, and a card in the viewer."""
    trig = violation.trigger
    detail = (f"{violation.prop_id} violated in {violation.room}: "
              f"condition held at smoke={trig['smoke_v']}, "
              f"temp={trig['temp_c']}, but the required state was not reached "
              f"within {violation.deadline - violation.created:.0f}s "
              f"(heater={trig['heater']}, buzzer={trig['buzzer']})")

    print(f"\n  [SPEC VIOLATION] {detail}\n")

    pi_guard.audit({"source": "monitor", "kind": "violation",
                    "property": violation.prop_id, "room": violation.room,
                    "trigger": trig,
                    "horizon_s": round(violation.deadline - violation.created, 1),
                    "detail": detail})


def push_alerts(mon):
    """One card per currently-violated property, merged with other writers."""
    cards = []
    seen = set()
    for v in reversed(mon.violations):          # newest first
        key = (v.prop_id, v.room)
        if key in seen:
            continue
        seen.add(key)
        text = next(p["text"] for p in spec.PROPERTIES if p["id"] == v.prop_id)
        cards.append({
            "id": f"{ALERT_PREFIX}{v.prop_id}-{v.room}",
            "severity": "critical",
            "title": f"Specification violated in {v.room}",
            "message": f"{v.prop_id}: {text}",
            "level": rooms.level(v.room), "room": v.room})
    twin.merge_alerts(ALERT_PREFIX, cards[:10])


# ---------------- main ----------------
#
# Guarded so this module can be imported — by a test, or by an analysis script
# replaying a recorded trace through the same Monitor class the live run uses.
# Verification code that can only be exercised by running it for real is
# verification code nobody checks.
running = True


def stop(*_):
    global running
    running = False


def main():
    global running
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    pi_guard.latest_run()          # attach to the run already in progress
    mon = Monitor()

    print(f"runtime monitor on {rooms.names()} — watching "
          f"{', '.join(p[0] for p in WATCH)}")
    print(f"poll {POLL_S}s, horizons: alarm {spec.H_ALARM:.0f}s, "
          f"clear {spec.H_CLEAR:.0f}s, comfort {spec.H_COMF:.0f}s. "
          f"Verdicts arrive up to one horizon late.")
    print(f"audit -> {pi_guard.AUDIT_PATH}. Ctrl-C to stop.\n")

    last_pending = None
    try:
        while running:
            fired = []
            for room in rooms.names():
                sample = sample_room(room)
                if sample["smoke_v"] is None and sample["temp_c"] is None:
                    continue                  # twin not reachable; try again
                fired += mon.step(sample)

            for v in fired:
                report(v)
            if fired:
                push_alerts(mon)

            # Show outstanding obligations counting down, so PENDING is visible
            # rather than looking like nothing is happening.
            p = mon.pending()
            if p != last_pending:
                if p:
                    print("  pending: " + ", ".join(
                        f"{i} {r} in {d:.0f}s" for i, r, d in p))
                last_pending = p

            time.sleep(POLL_S)
    finally:
        print("\n--- monitor summary ---")
        for prop_id, c in mon.counts.items():
            mark = "ok" if c["violated"] == 0 else "VIOLATED"
            print(f"  {prop_id:<20} satisfied {c['satisfied']:>4}   "
                  f"violated {c['violated']:>4}   {mark}")
        print(json.dumps(mon.counts))


if __name__ == "__main__":
    main()
