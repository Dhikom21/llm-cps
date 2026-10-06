"""
agent_tools.py — what the model is allowed to ask for and allowed to do.

The snapshot mode in llm_edge_agent.py TELLS the model everything up front.
This module is the other half: the model can ASK. That is the difference
between a program that calls an LLM and an agent.

Four tools:

    read_sensors    current values for one room, or all of them
    query_history   arbitrary read-only SQL over the readings table
    set_actuator    the only route to a pin, and it goes through pi_guard
    create_alert    put a card on the operator's screen

--------------------------------------------------------------------------
The point of the split
--------------------------------------------------------------------------
read_sensors and query_history are PERCEPTION — they can return nothing useful,
but they cannot do harm. set_actuator is ACTION, and every call is validated and
recorded by pi_guard before anything physical happens. A model may call the
first two as often as it likes; it may never reach a pin except through the
third, and never without a written reason.

--------------------------------------------------------------------------
A new failure surface, deliberately exposed
--------------------------------------------------------------------------
Giving the model a query tool creates a hallucination mode that snapshot mode
cannot produce: writing a query, receiving an empty result or an error, and then
reporting a finding anyway. Every tool call and its result is written to the
audit log precisely so that can be checked afterwards — "the agent said the
temperature had been climbing for ten minutes" is checkable against "the query
it ran returned zero rows".
"""
import json

import history
import pi_guard
import rooms

# OpenAI-compatible tool schemas. The description text is the only instruction
# the model gets about each tool, so it carries the constraints too — an
# argument spelled out here is one the model is less likely to invent.
SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_sensors",
            "description": "Current temperature and smoke for one room, or for "
                           "every room if room is omitted. Includes a short "
                           "recent history and the rate of change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string",
                             "description": f"One of {rooms.names()}. Omit for all."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_history",
            "description": "Run a read-only SELECT against the readings table. "
                           "Use for questions the snapshot cannot answer, such "
                           "as how long a condition has lasted or what a value "
                           "was some minutes ago.\n\n"
                           + history.SCHEMA_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string",
                            "description": "A single SELECT statement. "
                                           "No other statement type is permitted."},
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_actuator",
            "description": "Change an actuator. The only way to affect the "
                           "physical world. ALL THREE arguments are required — "
                           "a call without \"state\" is rejected and nothing "
                           "happens. Example: "
                           "{\"actuator\": \"A109/buzzer\", \"state\": \"on\", "
                           "\"reason\": \"A109 smoke_v 3.00 above threshold 1.0\"}",
            "parameters": {
                "type": "object",
                "properties": {
                    "actuator": {
                        "type": "string",
                        "description": "'<room>/<actuator>', e.g. 'A109/heater'. "
                                       "Valid: " + ", ".join(
                            rooms.actuator_id(r, a)
                            for r in rooms.names() for a in rooms.ACTUATORS),
                    },
                    "state": {"type": "string", "enum": ["on", "off"]},
                    "reason": {
                        "type": "string",
                        "description": "At least 5 characters. Cite the room "
                                       "and the values you observed. Do not "
                                       "state anything you did not read.",
                    },
                },
                "required": ["actuator", "state", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_alert",
            "description": "Raise an alert card for a human operator. Does not "
                           "change anything physical.",
            "parameters": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string",
                                 "enum": ["info", "warning", "critical"]},
                    "room": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["severity", "message"],
            },
        },
    },
]


def _sensors_for(building, room):
    return {
        "room": room,
        "temp_c": building.read_temperature(room),
        "smoke_v": building.read_smoke(room),
        "comfort_band": list(rooms.band(room)),
        "smoke_threshold": rooms.SMOKE_THRESHOLD,
        "recent": history.window(room),
        "trend": history.trend(room),
        **building.state(room),
    }


def dispatch(name, args, building, snapshot, source="llm"):
    """Run one tool call. Returns a JSON-serialisable result.

    Never raises: a tool that crashes the loop teaches the model nothing and
    loses the run. Errors come back as data the model can read and react to,
    which is itself worth observing — a good agent retries or reports; a poor
    one carries on as though the call succeeded.
    """
    try:
        if name == "read_sensors":
            room = args.get("room")
            if room:
                if room not in rooms.ROOMS:
                    return {"error": f"no such room: {room}",
                            "known": rooms.names()}
                return _sensors_for(building, room)
            return {r: _sensors_for(building, r) for r in rooms.names()}

        if name == "query_history":
            try:
                return history.query(args.get("sql", ""))
            except history.QueryRefused as exc:
                # Refusal is NOT an empty result, and must not look like one.
                return {"error": str(exc), "row_count": None}

        if name == "set_actuator":
            applied, code, detail = pi_guard.apply(
                building,
                {"actuator": args.get("actuator"),
                 "state": args.get("state"),
                 "reason": args.get("reason")},
                snapshot, source=source)
            return {"applied": applied, "code": code, "detail": detail}

        if name == "create_alert":
            import twin
            card = {"id": f"agent-{args.get('room', 'building')}",
                    "severity": args.get("severity", "info"),
                    "title": f"Agent alert: {args.get('room', 'building')}",
                    "message": str(args.get("message", ""))[:300]}
            room = args.get("room")
            if room in rooms.ROOMS:
                card["level"] = rooms.level(room)
                card["room"] = room
            twin._put("/api/alerts", [card])
            pi_guard.audit({"source": source, "applied": True,
                            "tool": "create_alert", "proposed": args,
                            "snapshot": snapshot})
            return {"raised": True}

        return {"error": f"no such tool: {name}",
                "known": [s["function"]["name"] for s in SCHEMAS]}

    except Exception as exc:                     # never let a tool kill the loop
        return {"error": f"tool failed: {exc}"}


def log_tool_call(name, args, result, snapshot, source="llm"):
    """Record perception calls too, not just actions.

    Without this the audit log shows what the agent DID but not what it KNEW,
    and H3 — a reason citing evidence that was never observed — becomes
    unfalsifiable. The whole value of the log is that the claim and the
    evidence sit side by side.
    """
    if name == "set_actuator":
        return                                   # pi_guard already logged it
    pi_guard.audit({"source": source, "tool": name, "args": args,
                    "result": json.loads(json.dumps(result, default=str))[:1]
                    if isinstance(result, list) else result,
                    "snapshot_read_at": snapshot.get("read_at")})
