"""
Level 7 — Tool Catalogue (lecture-4 §Tool Use and Function Calling)

Implements the four design principles from lecture-4 §Tool Design for Building Control:
  1. One tool per concern    — six narrow tools instead of one big "do everything".
  2. Descriptive names + docs— every tool's docstring is what the LLM actually reads.
  3. Validate inputs         — never trust LLM arguments; guardrails sit here.
  4. Return rich results     — every result carries context for the next Thought.

Also implements the audit trail (lecture-4 §Audit Trail):
every tool call writes (agent_id, step='action', tool, args, result) to the
`decisions` table in TimescaleDB.

The tool registry exposes:
  * SCHEMAS   — JSON-schema definitions in OpenAI/Ollama tool-calling format
                (the same format ReAct prompts also use)
  * call(...) — the executor: validates args, applies guardrails, runs the
                tool, returns a Result dict
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import psycopg2
import requests

import db

BUILDSIM = "http://localhost:9090"


# ---------- audit log ----------

def _audit(agent_id: str, step: str, *,
           tool: Optional[str] = None,
           args: Optional[Dict] = None,
           result: Optional[Dict] = None,
           text: Optional[str] = None,
           success: bool = True) -> None:
    """Append one row to `decisions`. Never raises — audit must not break the agent."""
    try:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO decisions (agent_id, step, tool, args, result, text, success)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (agent_id, step, tool,
                  psycopg2.extras.Json(args) if args is not None else None,
                  psycopg2.extras.Json(result) if result is not None else None,
                  text, success))
    except Exception as e:
        # Audit failure must NOT break the agent. Print so we see it during dev.
        print(f"[audit] failed: {e}")


# psycopg2.extras must be imported separately
import psycopg2.extras  # noqa: E402


# ---------- result type ----------

@dataclass
class Result:
    success: bool
    data: Dict
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        d = {"success": self.success, "data": self.data}
        if self.error:
            d["error"] = self.error
        return d


# ---------- tool: read_sensors ----------

def _tool_read_sensors(agent_id: str, room: str,
                       sensor_types: Optional[List[str]] = None,
                       last_seconds: int = 30) -> Result:
    """Reads recent sensor values from TimescaleDB for a given room.
    sensor_types is optional; if omitted, returns all types."""
    if not room:
        return Result(False, {}, error="room is required")
    if last_seconds < 1 or last_seconds > 3600:
        return Result(False, {}, error="last_seconds must be 1..3600")

    sql = """
        SELECT type, value, ts
        FROM readings
        WHERE room = %s
          AND ts > now() - (%s * INTERVAL '1 second')
          {tf}
        ORDER BY ts ASC
    """
    params = [room, last_seconds]
    type_filter = ""
    if sensor_types:
        placeholders = ",".join(["%s"] * len(sensor_types))
        type_filter = f" AND type IN ({placeholders})"
        params.extend(sensor_types)
    sql = sql.format(tf=type_filter)

    try:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as e:
        return Result(False, {}, error=f"db error: {e}")

    # Aggregate per type
    by_type: Dict[str, List[float]] = {}
    for typ, val, _ts in rows:
        by_type.setdefault(typ, []).append(val)

    summary = {}
    for typ, vals in by_type.items():
        if not vals: continue
        summary[typ] = {
            "current":   round(vals[-1], 3),
            "mean":      round(sum(vals) / len(vals), 3),
            "min":       round(min(vals), 3),
            "max":       round(max(vals), 3),
            "samples":   len(vals),
            "trend":     round(vals[-1] - vals[0], 3) if len(vals) > 1 else 0.0,
        }

    return Result(True, {
        "room": room,
        "window_seconds": last_seconds,
        "sensors": summary,
        "no_data": len(rows) == 0,
    })


# ---------- tool: set_actuator (with HARD GUARDRAILS) ----------

# Whitelist of legal actuator-id / state combinations.
# This is the §Guardrails section in lecture-4. The LLM is not trusted to
# pick safe values — these rules apply BEFORE the tool call goes out.
ACTUATOR_RULES = {
    "level4-heater-A109-state": {"states": {"on", "off"}},
    # Add more as you grow the stack:
    # "sprinkler-A2306-state":     {"states": {"on", "off"}},
    # "firedoor-A2306-state":      {"states": {"locked", "unlocked"}},
}

def _tool_set_actuator(agent_id: str, actuator_id: str,
                       state: str, reason: str = "") -> Result:
    """Sets a building actuator state. Validates against the whitelist before
    sending the command — the LLM cannot make up actuator ids or states."""
    rule = ACTUATOR_RULES.get(actuator_id)
    if rule is None:
        _audit(agent_id, "guardrail_blocked",
               tool="set_actuator", args={"actuator_id": actuator_id, "state": state},
               text="unknown actuator_id", success=False)
        return Result(False, {}, error=f"unknown actuator_id: {actuator_id}")

    if state not in rule["states"]:
        _audit(agent_id, "guardrail_blocked",
               tool="set_actuator", args={"actuator_id": actuator_id, "state": state},
               text=f"illegal state for this actuator; allowed={sorted(rule['states'])}",
               success=False)
        return Result(False, {}, error=f"illegal state {state!r}; allowed={sorted(rule['states'])}")

    if not reason or len(reason) < 5:
        _audit(agent_id, "guardrail_blocked",
               tool="set_actuator", args={"actuator_id": actuator_id, "state": state},
               text="reason is required and must be at least 5 characters",
               success=False)
        return Result(False, {}, error="reason is required and must be >= 5 chars")

    try:
        r = requests.put(
            f"{BUILDSIM}/api/actuators/{actuator_id}/state",
            json={"state": state}, timeout=2.0)
        if r.status_code != 200:
            return Result(False, {"status_code": r.status_code},
                          error=f"HTTP {r.status_code}: {r.text[:120]}")
    except requests.RequestException as e:
        return Result(False, {}, error=f"BuildSim unreachable: {e}")

    return Result(True, {
        "actuator_id": actuator_id,
        "state": state,
        "applied_at": "now",
        "reason_recorded": reason,
    })


# ---------- tool: highlight_rooms ----------

def _tool_highlight_rooms(agent_id: str,
                          rooms: List[Dict[str, Any]],
                          session_id: Optional[str] = None) -> Result:
    """Highlights rooms in the BuildSim 3D viewer.
    rooms is a list of {"room_id": int, "color": "#hex", "opacity": 0.0-1.0}.
    Uses the most-recently-active browser session if session_id not given."""
    if not session_id:
        try:
            r = requests.get(f"{BUILDSIM}/api/sessions", timeout=2.0).json()
            r.sort(key=lambda s: s.get("last_ws_active", ""), reverse=True)
            if not r:
                return Result(False, {}, error="no active session — open the viewer first")
            session_id = r[0]["id"]
        except requests.RequestException as e:
            return Result(False, {}, error=f"BuildSim unreachable: {e}")

    try:
        r = requests.put(
            f"{BUILDSIM}/api/sessions/{session_id}/highlights",
            json=rooms, timeout=2.0)
        if r.status_code != 200:
            return Result(False, {"status_code": r.status_code},
                          error=f"HTTP {r.status_code}")
    except requests.RequestException as e:
        return Result(False, {}, error=f"BuildSim unreachable: {e}")

    return Result(True, {"session_id": session_id, "rooms_highlighted": len(rooms)})


# ---------- tool: find_route (evacuation) ----------

def _tool_find_route(agent_id: str, from_room: str, to_room: str,
                     graph_type: str = "walkable") -> Result:
    """Computes shortest walkable path between two rooms using BuildSim's
    navigation graph. Returns the waypoints and total distance."""
    if graph_type not in {"adjacency", "walkable"}:
        return Result(False, {}, error="graph_type must be 'adjacency' or 'walkable'")
    try:
        r = requests.get(
            f"{BUILDSIM}/api/graph/route",
            params={"from_name": from_room, "to_name": to_room, "type": graph_type},
            timeout=3.0)
        if r.status_code != 200:
            return Result(False, {"status_code": r.status_code},
                          error=f"HTTP {r.status_code}: {r.text[:120]}")
        body = r.json()
    except requests.RequestException as e:
        return Result(False, {}, error=f"BuildSim unreachable: {e}")

    return Result(True, {
        "from_room": from_room, "to_room": to_room,
        "distance":  body.get("distance"),
        "waypoints": len(body.get("path", [])),
        "first":     body["path"][0]["name"]  if body.get("path") else None,
        "last":      body["path"][-1]["name"] if body.get("path") else None,
    })


# ---------- tool: create_alert ----------

def _tool_create_alert(agent_id: str, severity: str, message: str,
                       room: Optional[str] = None) -> Result:
    """Records an alert in the alerts table. severity is 'info'|'warning'|'critical'."""
    if severity not in {"info", "warning", "critical"}:
        return Result(False, {}, error="severity must be info|warning|critical")
    if not message:
        return Result(False, {}, error="message is required")

    try:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO alerts (agent_id, severity, room, message)
                VALUES (%s, %s, %s, %s)
                RETURNING ts
            """, (agent_id, severity, room, message))
            ts = cur.fetchone()[0]
    except Exception as e:
        return Result(False, {}, error=f"db error: {e}")

    return Result(True, {"severity": severity, "room": room,
                         "message": message, "ts": str(ts)})


# ---------- tool: query_history (raw SQL — useful for the agent to introspect) ----------

ALLOWED_SQL_PREFIX = "SELECT"

def _tool_query_history(agent_id: str, sql: str,
                        max_rows: int = 50) -> Result:
    """Run a read-only SQL query against TimescaleDB. Only SELECT is allowed.
    Useful for the agent to introspect: 'how many readings in the last hour?',
    'show me the past 5 actuator changes', etc."""
    s = sql.strip()
    if not s.upper().startswith(ALLOWED_SQL_PREFIX):
        return Result(False, {}, error="only SELECT queries are permitted")
    if max_rows > 200:
        max_rows = 200
    if "LIMIT" not in s.upper():
        s = s.rstrip(";") + f" LIMIT {max_rows}"

    try:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(s)
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        return Result(False, {}, error=f"sql error: {e}")

    # Convert non-JSON-friendly types
    for r in rows:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()

    return Result(True, {"rowcount": len(rows), "rows": rows})


# ---------- registry ----------

SCHEMAS: List[Dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_sensors",
            "description": ("Read recent sensor values for a room from the "
                            "time-series database. Returns per-type summary "
                            "{current, mean, min, max, trend, samples}."),
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string",
                             "description": "Room name, e.g. 'A109'"},
                    "sensor_types": {"type": "array", "items": {"type": "string"},
                                     "description": "Optional filter, e.g. ['temperature','co2']"},
                    "last_seconds": {"type": "integer",
                                     "description": "Window size in seconds (1..3600)"},
                },
                "required": ["room"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_actuator",
            "description": ("Command a building actuator. Validates against a "
                            "whitelist before sending; rejects unknown ids or "
                            "illegal states. 'reason' is required and audited."),
            "parameters": {
                "type": "object",
                "properties": {
                    "actuator_id": {"type": "string"},
                    "state":       {"type": "string"},
                    "reason":      {"type": "string",
                                    "description": "Human-readable reason (>=5 chars)"},
                },
                "required": ["actuator_id", "state", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "highlight_rooms",
            "description": "Set coloured highlights on rooms in the 3D viewer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rooms": {"type": "array",
                              "items": {"type": "object",
                                        "properties": {
                                            "room_id": {"type": "integer"},
                                            "color":   {"type": "string"},
                                            "opacity": {"type": "number"}}}},
                },
                "required": ["rooms"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_route",
            "description": "Compute shortest walkable path between two rooms.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_room": {"type": "string"},
                    "to_room":   {"type": "string"},
                },
                "required": ["from_room", "to_room"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_alert",
            "description": "Record an alert with severity info|warning|critical.",
            "parameters": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string",
                                 "enum": ["info", "warning", "critical"]},
                    "message":  {"type": "string"},
                    "room":     {"type": "string"},
                },
                "required": ["severity", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_history",
            "description": "Run a read-only SELECT against TimescaleDB.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"},
                               "max_rows": {"type": "integer"}},
                "required": ["sql"],
            },
        },
    },
]

EXECUTORS: Dict[str, Callable[..., Result]] = {
    "read_sensors":     _tool_read_sensors,
    "set_actuator":     _tool_set_actuator,
    "highlight_rooms":  _tool_highlight_rooms,
    "find_route":       _tool_find_route,
    "create_alert":     _tool_create_alert,
    "query_history":    _tool_query_history,
}


def call(agent_id: str, name: str, args: Dict) -> Result:
    """Universal entry point. Validates the name, dispatches to the executor,
    audits the action+observation. The agent calls only this function."""
    fn = EXECUTORS.get(name)
    if fn is None:
        result = Result(False, {}, error=f"unknown tool: {name}")
        _audit(agent_id, "action", tool=name, args=args, success=False)
        _audit(agent_id, "observation", tool=name, result=result.to_dict(), success=False)
        return result

    _audit(agent_id, "action", tool=name, args=args)
    try:
        result = fn(agent_id=agent_id, **args)
    except TypeError as e:
        result = Result(False, {}, error=f"bad arguments: {e}")
    _audit(agent_id, "observation", tool=name, result=result.to_dict(),
           success=result.success)
    return result


if __name__ == "__main__":
    # Smoke test
    db.ensure_schema()
    print("read_sensors:")
    print(call("smoke-test", "read_sensors",
               {"room": "A109", "last_seconds": 30}).to_dict())
    print()
    print("query_history:")
    print(call("smoke-test", "query_history",
               {"sql": "SELECT count(*) AS n FROM readings"}).to_dict())
