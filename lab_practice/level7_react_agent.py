"""
Level 7 — ReAct agent with explicit Thought / Action / Observation logging,
tool-calling via Ollama, and graceful degradation.

Maps directly onto lecture-4 sections:

  Perceive          : pull a sensor snapshot from TimescaleDB (the read_sensors tool)
  Reason            : LLM produces a Thought (logged) + zero-or-more tool_calls
  Plan              : the LLM's chosen tool sequence is the plan
  Act               : we execute each tool through the validating registry
  Observe           : the tool result is fed back into the next LLM turn
  Update            : every step writes to the `decisions` audit hypertable

If the LLM is unavailable (Ollama down, model missing, timeout, non-JSON output),
the agent falls back to a deterministic rule. This is lecture-4 §Graceful Degradation.

Pre-requisites:
  - BuildSim, Mosquitto, TimescaleDB running
  - level4_sensor.py + level4_consumer.py running
  - Ollama running with a tool-calling model:
        ollama pull llama3.2:3b
    (gemma2:2b also works but has weaker tool-call ability — we accept both.)

Run:
  python level7_react_agent.py
"""
import json
import signal
import sys
import time
from typing import Any, Dict, List, Optional

import requests

import db
import level7_tools as tools


AGENT_ID    = "level7-react-A109"
BASE        = "http://localhost:9090"
OLLAMA_URL  = "http://localhost:11434/api/chat"
MODEL       = "llama3.2:3b"        # change to whatever you have pulled
CYCLE_S     = 8.0                  # one full perceive→reason→act loop per N seconds
MAX_TOOL_HOPS = 4                  # max tool calls per cycle (loop guard)
LLM_TIMEOUT = 15.0                 # hard deadline; on timeout we fall back to rule
ROOM        = "A109"
HEATER_ID   = "level4-heater-A109-state"


SYSTEM_PROMPT = f"""You are the autonomous HVAC agent for room {ROOM}.

Your goal: keep the room temperature inside the comfort band 20.0–23.0 °C
while logging your reasoning for the audit trail.

You may call these tools (and only these):
  read_sensors   — pull recent sensor stats for a room
  set_actuator   — turn the heater on/off (id={HEATER_ID})
  create_alert   — record an alert (severity: info|warning|critical)
  query_history  — run a SELECT against the time-series DB
  highlight_rooms — colour a room in the 3D viewer
  find_route     — compute a walkable path between two rooms

Workflow each cycle:
  1. Call read_sensors first to perceive current state.
  2. Decide: does the heater need to change? If unchanged, do nothing.
  3. If changing, call set_actuator with a clear, audit-worthy reason.
  4. If anomaly (e.g., trend > +0.5 C/s, no data, sensor stuck): create_alert.
  5. Reply with a short final summary in plain text, no JSON.

Rules:
  - Never command actuators you don't have permission for.
  - Never set a heater state to anything other than 'on' or 'off'.
  - Always include a >=5-char 'reason' when calling set_actuator.
  - Be conservative — prefer "keep current state" over flipping repeatedly.
"""


# ---------- LLM call ----------

def _llm_chat(messages: List[Dict],
              available_tools: List[Dict]) -> Dict:
    """One call to Ollama with the OpenAI-compatible tool-calling schema.
    Returns the model's `message` dict ({content, tool_calls?})."""
    r = requests.post(OLLAMA_URL,
        json={
            "model":   MODEL,
            "messages": messages,
            "tools":    available_tools,
            "stream":   False,
        },
        timeout=LLM_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"ollama HTTP {r.status_code}: {r.text[:160]}")
    return r.json()["message"]


# ---------- fallback rule (graceful degradation) ----------

def _fallback_decide() -> Optional[Dict]:
    """Pure-rule heater decision. Used when the LLM is unavailable or misbehaves.
    Returns a set_actuator tool-call args dict, or None for 'do nothing'."""
    sensors = tools.call(AGENT_ID, "read_sensors",
                         {"room": ROOM, "sensor_types": ["temperature"],
                          "last_seconds": 30})
    if not sensors.success or sensors.data.get("no_data"):
        tools.call(AGENT_ID, "create_alert",
                   {"severity": "warning",
                    "message": "fallback: no sensor data for A109",
                    "room": ROOM})
        return None
    temp = sensors.data["sensors"].get("temperature", {})
    avg = temp.get("mean")
    if avg is None:
        return None
    if avg < 20.0:
        return {"actuator_id": HEATER_ID, "state": "on",
                "reason": f"fallback rule: avg {avg:.2f} below comfort low (20.0)"}
    if avg > 23.0:
        return {"actuator_id": HEATER_ID, "state": "off",
                "reason": f"fallback rule: avg {avg:.2f} above comfort high (23.0)"}
    return None


# ---------- the ReAct cycle ----------

def cycle(cycle_num: int) -> None:
    """One Perceive → Reason → Plan → Act → Observe → Update cycle."""
    print(f"\n=== cycle {cycle_num} === {time.strftime('%H:%M:%S')}")

    # PERCEIVE (first tool we let the agent invoke, but we seed the conversation
    # with the snapshot already so the model has context before its first turn).
    snapshot = tools.call(AGENT_ID, "read_sensors",
                          {"room": ROOM,
                           "sensor_types": ["temperature"],
                           "last_seconds": 30})

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",
         "content": (f"Cycle {cycle_num}. Snapshot just taken:\n"
                     f"{json.dumps(snapshot.data, indent=2)}\n\n"
                     "Decide what to do. Use tools as needed.")},
    ]

    # REASON + ACT loop, up to MAX_TOOL_HOPS times
    for hop in range(MAX_TOOL_HOPS):
        try:
            msg = _llm_chat(messages, tools.SCHEMAS)
        except Exception as e:
            print(f"  [llm] failed ({e}) — falling back to rule")
            tools._audit(AGENT_ID, "fallback", text=str(e), success=False)
            fb = _fallback_decide()
            if fb:
                tools.call(AGENT_ID, "set_actuator", fb)
            return

        thought = (msg.get("content") or "").strip()
        if thought:
            print(f"  [thought] {thought}")
            tools._audit(AGENT_ID, "thought", text=thought)

        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            # Model said its final answer — done for this cycle.
            tools._audit(AGENT_ID, "final", text=thought or "(no content)")
            return

        # Append the assistant message so the model sees its own tool_calls next turn
        messages.append({"role": "assistant",
                         "content": thought,
                         "tool_calls": tool_calls})

        # ACT each tool call, OBSERVE results
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name")
            args = fn.get("arguments")
            if isinstance(args, str):
                try: args = json.loads(args)
                except Exception:
                    args = {}
            if not isinstance(args, dict):
                args = {}

            print(f"  [action] {name}({json.dumps(args)[:120]})")
            result = tools.call(AGENT_ID, name, args)
            print(f"  [observ] success={result.success} {str(result.data)[:120]}")

            # Feed the tool result back into the conversation
            messages.append({"role": "tool",
                             "name": name,
                             "content": json.dumps(result.to_dict())})

    print("  [warn] reached MAX_TOOL_HOPS — stopping this cycle")
    tools._audit(AGENT_ID, "final", text="max tool hops reached", success=False)


# ---------- main ----------

def cleanup(*_):
    print("\nstopping agent.")
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

print(f"ReAct agent {AGENT_ID} starting. model={MODEL}, cycle={CYCLE_S}s.")
print("ensure schema (decisions + alerts tables) ...")
db.ensure_schema()

n = 0
while True:
    n += 1
    try:
        cycle(n)
    except Exception as e:
        print(f"  [error] cycle crashed: {e}")
        tools._audit(AGENT_ID, "final", text=f"crash: {e}", success=False)
    time.sleep(CYCLE_S)
