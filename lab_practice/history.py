"""
history.py — the data pipeline on the Pi: the past, as opposed to the present.

BuildSim holds only the latest value of each sensor. That is enough to control a
room but not enough to reason about one: it cannot tell 2.4 V rising fast from
2.4 V holding steady. This module keeps the readings so that question can be
answered.

Three consumers, deliberately separate:

    record()    every agent cycle writes here           (the write path)
    window()    a compact recent series for the prompt   (option 1: push)
    query()     arbitrary SELECT for the model's tool    (option 2: pull)

--------------------------------------------------------------------------
Why SQLite and not DuckDB
--------------------------------------------------------------------------
The simulated stack in this repo uses DuckDB. On the Pi, SQLite is in the
standard library — nothing to install, nothing to go wrong on arm64, and the
query surface the agent sees is SQL either way. The schema is deliberately flat
so the SQL the model writes would work unchanged against the DuckDB version.

--------------------------------------------------------------------------
MQTT
--------------------------------------------------------------------------
Each reading is also published to MQTT when a broker is reachable, which is how
the course material wires sensors to consumers. It is optional and failure is
silent: the broker is a distribution mechanism, not the system of record. The
database write is what matters, and it is local.

    sudo apt install -y mosquitto mosquitto-clients
    sudo systemctl enable --now mosquitto
"""
import os
import sqlite3
import threading
import time

DB_PATH   = os.environ.get("HISTORY_DB", "pi_readings.sqlite")
RETAIN_S  = float(os.environ.get("HISTORY_RETAIN_S", "3600"))   # 1 hour
WINDOW_N  = int(os.environ.get("HISTORY_WINDOW_N", "6"))

# The trend window must span several cycles or it contains one sample and the
# slope is undefined. On-CPU inference pushes cycles past 30 s, so a 30 s window
# silently reported "no trend" for every reading — the agent then could not see
# a ramp at all, which looked like a model failure and was ours.
TREND_WINDOW_S = float(os.environ.get("TREND_WINDOW_S", "180"))
QUERY_MAX_ROWS = 200

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

# Whether record() also announces the reading on MQTT.
#
# True for the single-process agents, which both sense and store. False for
# pi_consumer.py, which is DOWNSTREAM of the bus: if it republished what it
# had just received it would feed itself in a loop.
PUBLISH_TO_MQTT = True

_lock = threading.Lock()
_conn = None
_mqtt = None


# ---------------- setup ----------------
def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                ts      REAL NOT NULL,
                room    TEXT NOT NULL,
                temp_c  REAL,
                smoke_v REAL
            )""")
        # One index, on the thing every query filters by. Without it the
        # model's queries get slower as the run goes on, which would show up
        # as inference latency and be blamed on the wrong component.
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_room_ts "
                      "ON readings(room, ts)")
        _conn.commit()
    return _conn


def _broker():
    global _mqtt
    if _mqtt is not None:
        return _mqtt
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"pi-history-{os.getpid()}")
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        client.loop_start()
        _mqtt = client
        print(f"[history] MQTT connected to {MQTT_HOST}:{MQTT_PORT}")
    except Exception as exc:
        print(f"[history] MQTT unavailable ({exc}) — recording locally only")
        _mqtt = False
    return _mqtt


# ---------------- write path ----------------
def record(room, temp_c, smoke_v, ts=None):
    """One reading. Called every cycle by whichever agent is running, so the
    baseline and the LLM produce comparable history."""
    ts = ts or time.time()
    with _lock:
        _db().execute(
            "INSERT INTO readings (ts, room, temp_c, smoke_v) VALUES (?,?,?,?)",
            (ts, room, temp_c, smoke_v))
        _db().commit()

    if not PUBLISH_TO_MQTT:
        return

    client = _broker()
    if client:
        level = "level0"
        payload = (f'{{"room":"{room}","temp_c":{temp_c if temp_c is not None else "null"},'
                   f'"smoke_v":{smoke_v},"ts":{ts:.0f}}}')
        try:
            client.publish(f"sensors/{level}/{room}/reading", payload)
        except Exception:
            pass        # distribution is best-effort; the DB write already won


def prune():
    """Drop anything older than the retention window.

    A control loop running for hours would otherwise grow the file without
    bound on a device with an SD card — and a full SD card takes the whole
    system down, not just the logging.
    """
    cutoff = time.time() - RETAIN_S
    with _lock:
        _db().execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
        _db().commit()


# ---------------- read path 1: the prompt window ----------------
def window(room, n=None, max_age_s=None):
    """The last n readings, oldest first — small enough to put in a prompt.

    This is what lets a model see a trend rather than a single instant. Note it
    is PUSHED: the agent decides what the model sees. Contrast query() below,
    where the model decides.

    Two things here are not cosmetic.

    MAX AGE. Without it, "the last six readings" could be six readings from
    yesterday's run. That actually happened: a previous fire left 3.0 V rows in
    the database, the agent restarted, and the model was handed a window full
    of old smoke readings alongside a current value of 0.10 V. It concluded
    smoke was above threshold — which looked like a hallucination and was us
    feeding it stale data. Readings older than the window are now dropped, and
    an empty list is the honest answer when nothing recent exists.

    AGE, NOT TIMESTAMP. A unix epoch means nothing to a language model: it
    cannot tell that 1791286450 was six minutes ago. Seconds-ago is immediately
    interpretable, and it makes "this reading is stale" something the model can
    actually notice.
    """
    n = n or WINDOW_N
    max_age = TREND_WINDOW_S if max_age_s is None else max_age_s
    now = time.time()
    with _lock:
        rows = _db().execute(
            "SELECT ts, temp_c, smoke_v FROM readings "
            "WHERE room = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
            (room, now - max_age, n)).fetchall()
    rows.reverse()
    return [{"age_s": round(now - ts, 1), "temp_c": t, "smoke_v": s}
            for ts, t, s in rows]


def trend_of(rows):
    """The slope of exactly the readings handed in — nothing re-queried.

    This exists so the number the model is given and the list the model is
    shown cannot disagree.

    They used to. `recent` returned at most WINDOW_N readings (about 35 s of
    coverage) while `trend` re-queried the whole TREND_WINDOW_S of 180 s, and
    the two were placed side by side in one payload with nothing saying they
    covered different spans. During a fire that gap mattered enormously: the
    smoke ramp lasted 20 s inside a 178 s window that was otherwise flat, so
    the published slope came out at 0.0163 V/s when the same six readings in
    `recent` gave 0.0876 V/s and the true ramp was 0.145 V/s. The model was
    handed a precomputed number five times worse than the list beside it, and
    no field told it the two were measured differently.

    Deriving the slope from the shown rows makes the payload checkable: if the
    model does the arithmetic itself it gets our answer. `rows` are window()'s
    output, oldest first, carrying age_s rather than a timestamp.
    """
    usable = [r for r in rows if r.get("age_s") is not None]
    if len(usable) < 2:
        return {"temp_c_per_s": None, "smoke_v_per_s": None,
                "samples": len(usable), "window_s": 0.0,
                "from": "the readings listed in `recent`"}

    span = usable[0]["age_s"] - usable[-1]["age_s"]   # oldest minus newest
    if span <= 0:
        return {"temp_c_per_s": None, "smoke_v_per_s": None,
                "samples": len(usable), "window_s": 0.0,
                "from": "the readings listed in `recent`"}

    def rate(key):
        a, b = usable[0].get(key), usable[-1].get(key)
        if a is None or b is None:
            return None
        return round((b - a) / span, 4)

    return {"temp_c_per_s": rate("temp_c"), "smoke_v_per_s": rate("smoke_v"),
            "samples": len(usable), "window_s": round(span, 1),
            "from": "the readings listed in `recent`"}


def trend(room, seconds=None):
    """Change per second over the last `seconds`, as a plain number.

    This is the LONG view, and it is kept for temperature. A room drifts over
    minutes and the DS18B20 quantises in 0.0625 degree steps, so a slope taken
    over the half-minute in `recent` is mostly quantisation noise. Smoke is the
    opposite case — see trend_of() for why a long window ruins it — which is
    why both are now published, each naming its own window.
    """
    seconds = TREND_WINDOW_S if seconds is None else seconds
    cutoff = time.time() - seconds
    with _lock:
        rows = _db().execute(
            "SELECT ts, temp_c, smoke_v FROM readings "
            "WHERE room = ? AND ts >= ? ORDER BY ts", (room, cutoff)).fetchall()
    if len(rows) < 2:
        return {"temp_c_per_s": None, "smoke_v_per_s": None, "samples": len(rows)}

    span = rows[-1][0] - rows[0][0]
    if span <= 0:
        return {"temp_c_per_s": None, "smoke_v_per_s": None, "samples": len(rows)}

    def rate(i):
        a, b = rows[0][i], rows[-1][i]
        if a is None or b is None:
            return None
        return round((b - a) / span, 4)

    return {"temp_c_per_s": rate(1), "smoke_v_per_s": rate(2),
            "samples": len(rows), "window_s": round(span, 1),
            "from": f"every reading in the last {seconds:.0f}s"}


# ---------------- read path 2: the model's tool ----------------
class QueryRefused(Exception):
    pass


def query(sql, max_rows=QUERY_MAX_ROWS):
    """Run a read-only SELECT on behalf of the agent.

    The model writes this SQL, so it is untrusted input and treated as such:

      - SELECT only; anything else is refused outright
      - one statement; no semicolon-chaining a DELETE onto a SELECT
      - row cap, so a careless query cannot exhaust memory on a Pi
      - a read-only connection, so even a flaw above cannot write

    Refusals raise rather than returning empty. An empty result and a rejected
    query must never look the same to the agent — otherwise a model can report
    "no readings found" when what actually happened is that it was stopped.
    """
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise QueryRefused("empty query")

    lowered = text.lower()
    if not lowered.startswith("select"):
        raise QueryRefused("only SELECT statements are permitted")
    if ";" in text:
        raise QueryRefused("only one statement per query")
    for word in ("insert", "update", "delete", "drop", "alter", "create",
                 "attach", "pragma", "vacuum"):
        if f" {word} " in f" {lowered} ":
            raise QueryRefused(f"keyword not permitted: {word}")

    uri = f"file:{os.path.abspath(DB_PATH)}?mode=ro"
    with _lock:
        ro = sqlite3.connect(uri, uri=True)
        try:
            cur = ro.execute(text)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(max_rows)
        except sqlite3.Error as exc:
            raise QueryRefused(f"SQL error: {exc}")
        finally:
            ro.close()

    return {"columns": cols,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": len(rows) >= max_rows}


SCHEMA_DESCRIPTION = """Table: readings
  ts      REAL  unix seconds
  room    TEXT  'A109' or 'A108'
  temp_c  REAL  degrees C, may be NULL if that sensor was missing
  smoke_v REAL  volts, clean air is about 0.10

Example:
  SELECT ts, temp_c, smoke_v FROM readings
  WHERE room='A109' AND ts > strftime('%s','now') - 60
  ORDER BY ts DESC LIMIT 20"""


if __name__ == "__main__":
    import json
    import rooms
    for room in rooms.names():
        print(room, "window:", json.dumps(window(room)))
        print(room, "trend :", json.dumps(trend(room)))
    print(json.dumps(query(
        "SELECT room, COUNT(*) n, MIN(ts), MAX(ts) FROM readings GROUP BY room"),
        indent=2))
