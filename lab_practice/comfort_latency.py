"""
comfort_latency.py — how long the bus takes to turn a temperature crossing
into a heater command.

    first published reading outside the band   ->   heater command applied
                        |<--------- this interval --------->|

pi_guard.response_times() does the same job for the smoke path, but it can
rely on inject_fire.py writing a `threshold_crossed` marker: the injector
knows the exact instant the simulated smoke passed 1.0 V, independently of
when anything sampled it.

Temperature has no injector. The room is warmed by hand and the real DS18B20
is the only witness, so the earliest knowable crossing is the first PUBLISHED
reading outside the band. That is the clock start used here.

    Consequence, and it matters when quoting the number:
    this measurement EXCLUDES the sensor period. The true crossing happened
    somewhere in the interval between the last in-band sample and the first
    out-of-band one — on average half a sensor period earlier, up to a full
    period in the worst case. The smoke figure's 6.6 s "waiting for the next
    sensor publish" term is therefore missing here and has to be added back
    separately. Reporting this interval as end-to-end latency would flatter
    the bus by roughly one sensor period.

What it prints per crossing:
    seconds        first out-of-band publish -> command applied
    reading_age_s  how stale the reading was when the shield applied it
    cycles         how many agent cycles fitted inside the gap

Usage:
    python3 comfort_latency.py                        # latest run, default DB
    python3 comfort_latency.py runs/run-...-llm.jsonl
    BAND_LO=24 BAND_HI=25 python3 comfort_latency.py
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

import pi_guard
import rooms

DB_PATH   = os.environ.get("DB_PATH", "pi_readings.sqlite")
CYCLE_S   = float(os.environ.get("CYCLE_S", "10.0"))
SENSOR_S  = float(os.environ.get("SENSOR_PERIOD_S", "5.0"))
# Only count a command that follows its crossing within this long. Beyond it
# the pairing is guesswork: another crossing, a restart or a shutdown command
# is at least as likely an explanation as a slow response.
MAX_PAIR_S = float(os.environ.get("MAX_PAIR_S", "180"))


def readings(db=DB_PATH):
    """Every stored reading, oldest first, as (ts, room, temp_c).

    85.0 is dropped. It is the DS18B20's power-on reset value, returned when
    a conversion is read before it finished or the parasite supply dipped —
    A108 has produced it intermittently on this rig. Left in, it would look
    like a violent crossing above any sane band and would be paired with
    whatever heater command came next, inventing a response time out of a
    wiring fault. Set KEEP_85=1 if a reading of exactly 85 C is ever real.
    """
    keep85 = os.environ.get("KEEP_85") == "1"
    con = sqlite3.connect(db)
    try:
        rows = [(t, r, c) for t, r, c in con.execute(
            "SELECT ts, room, temp_c FROM readings "
            "WHERE temp_c IS NOT NULL ORDER BY ts")]
    finally:
        con.close()
    if keep85:
        return rows
    dropped = sum(1 for _, _, c in rows if c == 85.0)
    if dropped:
        print(f"# note: ignored {dropped} reading(s) of exactly 85.0 C "
              f"(DS18B20 reset value)", file=sys.stderr)
    return [r for r in rows if r[2] != 85.0]


def crossings(rows, window=None):
    """Where the published stream first leaves the band, per room.

    Only the transition is a crossing. A room that is already too cold when
    the run starts has not crossed anything — there was no event for the
    agent to respond to, and timing from the first reading would measure
    startup, not response. Those are reported separately.
    """
    out, initial, state = [], [], {}
    for ts, room, temp in rows:
        if window and not (window[0] <= ts <= window[1]):
            continue
        lo, hi = rooms.band(room)
        where = "low" if temp < lo else "high" if temp > hi else "in"
        was = state.get(room)
        state[room] = where

        if where == "in" or where == was:
            continue
        want = "on" if where == "low" else "off"
        if was is None:
            initial.append({"room": room, "at": ts, "temp_c": temp,
                            "wanted": want})
        else:
            out.append({"room": room, "at": ts, "temp_c": temp,
                        "from": was, "to": where, "wanted": want})
    return out, initial


def _records(paths):
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except OSError:
            continue


def heater_commands(paths):
    """Applied heater commands, oldest first, from one or more audit files.

    Which file holds them depends on the architecture, which is why this
    takes a list. In DIRECT mode the agent calls pi_guard itself and the
    applied record lands in the agent's own audit. In BUS mode the agent only
    publishes a request; pi_actuator runs the shield and writes the applied
    record to ITS audit. So a bus run's agent file contains tool calls and
    nothing else, and pointing this at the agent file alone finds no commands
    at all — which looks like "the model never acted" when the model acted
    perfectly well.

    Pass every audit file from the run and let this sort it out.

    Shutdown commands are dropped: "agent stopping" switches everything off
    regardless of temperature, and pairing one with a crossing would record
    a response that never happened.
    """
    out = []
    for rec in _records(paths):
        if not (rec.get("applied") and rec.get("actuator") == "heater"):
            continue
        if "stopping" in str(rec.get("detail", "")).lower():
            continue
        ts = rec.get("ts")
        if not ts:
            continue
        out.append({"at": datetime.fromisoformat(ts).timestamp(),
                    "room": rec.get("room"),
                    "state": rec.get("state"),
                    "reading_age_s": rec.get("reading_age_s"),
                    "physical": rec.get("physical"),
                    "detail": rec.get("detail")})
    return sorted(out, key=lambda r: r["at"])


def readings_from_audit(paths):
    """Rebuild the published reading stream out of the agent's own audit.

    Every read_sensors record carries `snapshot_read_at` plus a `recent`
    window whose entries are stamped with `age_s`, so each published reading
    can be recovered as (snapshot_read_at - age_s). Successive cycles overlap,
    hence the dict: the same publish is seen several times and must collapse
    to one entry.

    This is a fallback for when the SQLite file was not kept. It is strictly
    worse than the database — it only covers what happened to be inside the
    trend window at each cycle, so early readings and anything dropped before
    the first cycle are missing — but it is enough to locate band crossings,
    and it comes from the same stream the agent actually saw.
    """
    seen = {}
    for rec in _records(paths):
        if rec.get("tool") != "read_sensors":
            continue
        base = rec.get("snapshot_read_at")
        if not base:
            continue
        for room, d in (rec.get("result") or {}).items():
            if not isinstance(d, dict):
                continue
            for r in d.get("recent") or []:
                if r.get("age_s") is None or r.get("temp_c") is None:
                    continue
                seen[(round(base - r["age_s"], 0), room)] = r["temp_c"]
    return sorted((ts, room, temp) for (ts, room), temp in seen.items())


def measure(paths, db=DB_PATH):
    if isinstance(paths, str):
        paths = [paths]
    cmds = heater_commands(paths)
    if not cmds:
        return {"error": "no applied heater commands in " + ", ".join(paths),
                "hint": "in bus mode these live in the ACTUATOR audit, not "
                        "the agent's — pass both files"}
    # Scope to the AGENT's session, not to the span of the commands.
    #
    # pi_actuator keeps one audit open for as long as the process lives, so a
    # single actuator file routinely covers several agent runs — including
    # earlier attempts made under a different BAND_LO/BAND_HI. Taking the
    # window from the commands would stretch it across all of them and then
    # judge those older readings against the band that happens to be in the
    # environment now, inventing crossings that the agent of the time was
    # never asked to respond to. The agent's own audit is session-scoped, so
    # when one is supplied it defines the window.
    agent_ts = [datetime.fromisoformat(r["ts"]).timestamp()
                for r in _records(paths) if r.get("tool") and r.get("ts")]
    if agent_ts:
        window = (min(agent_ts) - 60, max(agent_ts) + 60)
        scope = "agent session"
    else:
        window = (cmds[0]["at"] - 300, cmds[-1]["at"] + 300)
        scope = "span of applied commands (no agent audit supplied)"
    cmds = [c for c in cmds if window[0] <= c["at"] <= window[1]]

    if db and os.path.exists(db):
        rows, source = readings(db), db
    else:
        rows, source = readings_from_audit(paths), "reconstructed from audit"
        print(f"# note: {db} not found, rebuilding the reading stream from "
              f"the audit's recent windows", file=sys.stderr)
    events, initial = crossings(rows, window)
    used, results, missed = set(), [], []

    for ev in events:
        hit = None
        for i, c in enumerate(cmds):
            if (i not in used and c["room"] == ev["room"]
                    and c["state"] == ev["wanted"]
                    and ev["at"] <= c["at"] <= ev["at"] + MAX_PAIR_S):
                hit, used = c, used | {i}
                break
        if hit is None:
            missed.append(ev)
            continue
        gap = hit["at"] - ev["at"]
        results.append({
            "room": ev["room"],
            "crossed": f"{ev['from']} -> {ev['to']}",
            "temp_c": ev["temp_c"],
            "commanded": hit["state"],
            "seconds": round(gap, 1),
            "reading_age_s": hit["reading_age_s"],
            "cycles": round(gap / CYCLE_S, 1),
            "physical": hit["physical"],
        })

    secs = sorted(r["seconds"] for r in results)
    return {
        "audit": paths,
        "readings_from": source,
        "window": scope,
        "window_utc": [datetime.utcfromtimestamp(window[0]).strftime("%H:%M:%S"),
                       datetime.utcfromtimestamp(window[1]).strftime("%H:%M:%S")],
        "responses": results,
        "no_command": missed,
        "at_startup_not_a_crossing": initial,
        "median_s": secs[len(secs) // 2] if secs else None,
        "worst_s": secs[-1] if secs else None,
        "excluded_sensor_period_s": SENSOR_S,
        "note": ("Clock starts at the first out-of-band PUBLISH, so up to "
                 f"{SENSOR_S:.1f}s of sensing delay is not counted."),
    }


if __name__ == "__main__":
    audits = sys.argv[1:] or [pi_guard.latest_run()]
    print("# audits " + ", ".join(audits))
    print("# bands  " + ", ".join(f"{r} {rooms.band(r)}" for r in rooms.names()))
    print(json.dumps(measure(audits), indent=2))
