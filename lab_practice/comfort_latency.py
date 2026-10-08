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


def heater_commands(path):
    """Applied heater commands from the audit, oldest first.

    Shutdown commands are dropped: "agent stopping" switches everything off
    regardless of temperature, and pairing one with a crossing would record
    a response that never happened.
    """
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
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
    except OSError:
        pass
    return sorted(out, key=lambda r: r["at"])


def measure(path, db=DB_PATH):
    cmds = heater_commands(path)
    if not cmds:
        return {"error": f"no applied heater commands in {path}"}
    window = (cmds[0]["at"] - 300, cmds[-1]["at"] + 300)

    events, initial = crossings(readings(db), window)
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
        "audit": path,
        "database": db,
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
    audit = sys.argv[1] if len(sys.argv) > 1 else pi_guard.latest_run()
    print(f"# audit  {audit}")
    print(f"# bands  " + ", ".join(f"{r} {rooms.band(r)}" for r in rooms.names()))
    print(json.dumps(measure(audit), indent=2))
