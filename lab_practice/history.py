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
QUERY_MAX_ROWS = 200

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

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
def window(room, n=None):
    """The last n readings, oldest first — small enough to put in a prompt.

    This is what lets a model see a trend rather than a single instant. Note it
    is PUSHED: the agent decides what the model sees. Contrast query() below,
    where the model decides.
    """
    n = n or WINDOW_N
    with _lock:
        rows = _db().execute(
            "SELECT ts, temp_c, smoke_v FROM readings WHERE room = ? "
            "ORDER BY ts DESC LIMIT ?", (room, n)).fetchall()
    rows.reverse()
    return [{"t": round(ts, 1), "temp_c": t, "smoke_v": s} for ts, t, s in rows]


def trend(room, seconds=30.0):
    """Change per second over the last `seconds`, as a plain number.

    Computed here rather than left for the model to infer from the series. Both
    are given: the arithmetic is cheap and reliable in Python and unreliable in
    a 3B model, and including it lets the experiments separate "could not see
    the trend" from "saw it and ignored it".
    """
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
            "samples": len(rows), "span_s": round(span, 1)}


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
