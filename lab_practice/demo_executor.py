"""
Demo executor — a ColonyOS executor that picks up 'agent_cycle' function
specs, asks an Ollama LLM what to do with the prompt, and executes the
LLM's tool calls against BuildSim.

Reuses the same tool patterns as level7 but reads its prompt from the
function spec args instead of from a perceive loop.

Run:
    python demo_executor.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests

try:
    from pycolonies import colonies_client
except ImportError:
    print("pycolonies is not installed. Run: pip install -r demo_requirements.txt")
    sys.exit(1)


# ---------- configuration ----------

COLONY_NAME    = os.environ.get("COLONIES_COLONY_NAME", "d7065e")
EXECUTOR_PRV   = os.environ.get("EXECUTOR_PRVKEY")
BUILDSIM       = os.environ.get("BUILDSIM_URL", "http://localhost:9090")

# ---- LLM backend ----
# LLM_BACKEND="openai"  → any OpenAI-compatible server (vLLM, OpenAI, etc.)
# LLM_BACKEND="ollama"  → local Ollama on the laptop
LLM_BACKEND    = os.environ.get("LLM_BACKEND", "openai").lower()

# Default URL depends on the backend. For openai-style, the URL must point
# at the FULL chat completions endpoint (i.e. include /v1/chat/completions).
if LLM_BACKEND == "openai":
    _default_url   = "http://carbon.eislab.se:8000/v1/chat/completions"
    _default_model = "google/gemma-4-E4B-it"
else:
    _default_url   = "http://localhost:11434/api/chat"
    _default_model = "gemma3:4b"

LLM_URL        = os.environ.get("LLM_URL",   _default_url)
LLM_MODEL      = os.environ.get("LLM_MODEL", _default_model)
LLM_API_KEY    = os.environ.get("LLM_API_KEY", "not-needed")
LLM_TIMEOUT_S  = int(os.environ.get("LLM_TIMEOUT", "30"))
MAX_TOOL_HOPS  = 4

if not EXECUTOR_PRV:
    print("EXECUTOR_PRVKEY is not set. See demo_README.md for setup.")
    sys.exit(1)


# ---------- helpers ----------

def get_active_session() -> Optional[str]:
    """Most recently active BuildSim browser session."""
    try:
        r = requests.get(f"{BUILDSIM}/api/sessions", timeout=2).json()
    except requests.RequestException:
        return None
    r.sort(key=lambda s: s.get("last_ws_active", ""), reverse=True)
    return r[0]["id"] if r else None


def find_room_id(name: str) -> Optional[int]:
    """Look up the integer room_id for a given room name like 'A2306' or '1542'."""
    try:
        # Try each floor
        for level in ("level0", "level1", "level2"):
            data = requests.get(f"{BUILDSIM}/api/building/floors/{level}",
                                timeout=2).json()
            for room in data.get("rooms", []):
                if room.get("name", "").strip().lower() == name.strip().lower():
                    return room.get("id")
    except requests.RequestException:
        pass
    return None


# ---------- tool implementations ----------

def tool_highlight_room(args: Dict) -> Dict:
    room    = args.get("room")
    color   = args.get("color", "#ff0000")
    opacity = float(args.get("opacity", 0.6))
    if not room:
        return {"ok": False, "error": "missing 'room'"}

    room_id = find_room_id(room)
    if room_id is None:
        return {"ok": False, "error": f"unknown room {room!r}"}

    sid = get_active_session()
    if sid is None:
        return {"ok": False, "error":
                "no active BuildSim session — open the viewer first"}

    try:
        r = requests.put(
            f"{BUILDSIM}/api/sessions/{sid}/highlights",
            json=[{"room_id": room_id, "color": color, "opacity": opacity}],
            timeout=2)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "room": room, "room_id": room_id,
            "color": color, "session": sid[:8]}


def tool_set_actuator(args: Dict) -> Dict:
    actuator_id = args.get("actuator_id")
    state       = args.get("state")
    if not actuator_id or not state:
        return {"ok": False, "error": "need 'actuator_id' and 'state'"}
    try:
        r = requests.put(
            f"{BUILDSIM}/api/actuators/{actuator_id}/state",
            json={"state": state}, timeout=2)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:80]}"}
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "actuator_id": actuator_id, "state": state}


def tool_read_sensor(args: Dict) -> Dict:
    """Read current sensor value via BuildSim. Either 'sensor_id' or
    ('equipment_id', 'type') is required."""
    sensor_id    = args.get("sensor_id")
    equipment_id = args.get("equipment_id")
    sensor_type  = args.get("type")

    try:
        if sensor_id:
            # No direct endpoint by sensor_id, so search equipment
            equips = requests.get(f"{BUILDSIM}/api/equipment",
                                  timeout=2).json()
            for eq in equips:
                for s in eq.get("sensors") or []:
                    if s.get("id") == sensor_id:
                        return {"ok": True, "value": s.get("value"),
                                "unit": s.get("unit"), "sensor_id": sensor_id}
            return {"ok": False, "error": f"sensor {sensor_id} not found"}

        if equipment_id:
            eq = requests.get(f"{BUILDSIM}/api/equipment/{equipment_id}",
                              timeout=2).json()
            sensors = eq.get("sensors") or []
            if sensor_type:
                sensors = [s for s in sensors if s.get("type") == sensor_type]
            if not sensors:
                return {"ok": False, "error": "no matching sensor"}
            s = sensors[0]
            return {"ok": True, "value": s.get("value"),
                    "unit": s.get("unit"), "sensor_id": s.get("id")}
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}

    return {"ok": False, "error": "need sensor_id or equipment_id+type"}


def tool_find_route(args: Dict) -> Dict:
    f = args.get("from_room")
    t = args.get("to_room")
    if not f or not t:
        return {"ok": False, "error": "need 'from_room' and 'to_room'"}
    try:
        r = requests.get(
            f"{BUILDSIM}/api/graph/route",
            params={"from_name": f, "to_name": t, "type": "walkable"},
            timeout=3)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        body = r.json()
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}

    # Display the route on the active session too
    sid = get_active_session()
    if sid:
        try:
            requests.put(f"{BUILDSIM}/api/sessions/{sid}/route",
                         json=body, timeout=2)
        except requests.RequestException:
            pass

    return {"ok": True, "from": f, "to": t,
            "distance": body.get("distance"),
            "waypoints": len(body.get("path") or [])}


TOOLS: Dict[str, Any] = {
    "highlight_room": tool_highlight_room,
    "set_actuator":   tool_set_actuator,
    "read_sensor":    tool_read_sensor,
    "find_route":     tool_find_route,
}

# ---------- tool schemas (OpenAI / Ollama function-calling format) ----------

TOOL_SCHEMAS: List[Dict] = [
    {
        "type": "function",
        "function": {
            "name": "highlight_room",
            "description": ("Colour a room on the 3D viewer. Room is a name "
                            "like 'A109' or '1542'."),
            "parameters": {
                "type": "object",
                "properties": {
                    "room":    {"type": "string"},
                    "color":   {"type": "string",
                                "description": "Hex like '#ff0000'"},
                    "opacity": {"type": "number"},
                },
                "required": ["room"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_actuator",
            "description": "Set an actuator state. Use the actuator's full id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "actuator_id": {"type": "string"},
                    "state":       {"type": "string"},
                },
                "required": ["actuator_id", "state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_sensor",
            "description": ("Read current value of a sensor. Either sensor_id "
                            "or (equipment_id + type)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "sensor_id":    {"type": "string"},
                    "equipment_id": {"type": "string"},
                    "type":         {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_route",
            "description": ("Compute walkable route between two rooms and "
                            "display it on the 3D viewer."),
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
]

SYSTEM_PROMPT = """You are a building-control agent.
The user types a natural-language request.
Translate it into one or more tool calls.
After tools complete, reply with one short sentence describing what you did.
If you can't fulfil the request safely, say so plainly.
"""


# ---------- LLM call ----------

def llm_chat(messages: List[Dict]) -> Dict:
    """One LLM call. Returns a normalised 'message' dict with content + tool_calls.

    Speaks two protocols:
      • openai  — vLLM, OpenAI, anything else with /v1/chat/completions.
                  Response: {"choices":[{"message":{"content":..., "tool_calls":[...]}}]}
      • ollama  — local Ollama at /api/chat.
                  Response: {"message":{"content":..., "tool_calls":[...]}}
    """
    body = {
        "model":    LLM_MODEL,
        "messages": messages,
        "tools":    TOOL_SCHEMAS,
        "stream":   False,
    }
    headers = {"Content-Type": "application/json"}
    if LLM_BACKEND == "openai":
        # vLLM accepts any string for the bearer; OpenAI requires a real key.
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    r = requests.post(LLM_URL, json=body, headers=headers,
                      timeout=LLM_TIMEOUT_S)
    if r.status_code != 200:
        raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:200]}")

    j = r.json()
    if LLM_BACKEND == "openai":
        # OpenAI-style: pull message out of choices[0]
        choices = j.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM returned no choices: {j}")
        return choices[0].get("message", {}) or {}
    # Ollama-style
    return j.get("message", {}) or {}


# ---------- agent cycle (ReAct loop) ----------

def run_cycle(prompt: str) -> str:
    """Handle one chat prompt. Returns a human-readable response."""
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ]

    final_text = ""
    for hop in range(MAX_TOOL_HOPS):
        try:
            msg = llm_chat(messages)
        except Exception as e:
            return f"LLM unavailable ({e})."

        final_text = (msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            return final_text or "(no response)"

        # Echo assistant message for context
        messages.append({"role": "assistant",
                         "content": final_text,
                         "tool_calls": tool_calls})

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name")
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            tool = TOOLS.get(name)
            if tool is None:
                result = {"ok": False, "error": f"unknown tool {name}"}
            else:
                try:
                    result = tool(args or {})
                except Exception as e:
                    result = {"ok": False, "error": str(e)}
            print(f"  [tool] {name}({json.dumps(args)[:80]}) -> {result}")
            messages.append({"role": "tool", "name": name,
                             "content": json.dumps(result)})

    return final_text or "Reached max tool hops without a final answer."


# ---------- ColonyOS executor main loop ----------

# Newer pycolonies returns a tuple from colonies_client(); older returns the
# client directly. Handle both transparently.
_cl_result = colonies_client()
if isinstance(_cl_result, tuple):
    client = _cl_result[0]
    print(f"[executor] pycolonies returned tuple of {len(_cl_result)} items; "
          f"using item [0] as client")
else:
    client = _cl_result

print(f"[executor] starting, colony={COLONY_NAME}")
print(f"[executor] LLM backend={LLM_BACKEND}, model={LLM_MODEL}, url={LLM_URL}")
print(f"[executor] BuildSim={BUILDSIM}")
print(f"[executor] waiting for agent_cycle work...")

while True:
    try:
        process = client.assign(COLONY_NAME, 5, EXECUTOR_PRV)
        if process is None:
            continue
        spec = process.spec
        print(f"[executor] got process {process.processid[:12]} "
              f"funcname={spec.funcname}")

        if spec.funcname != "agent_cycle":
            client.fail(process.processid,
                        [f"unknown function {spec.funcname}"], EXECUTOR_PRV)
            continue

        prompt = spec.args[0] if spec.args else ""
        if not prompt:
            client.fail(process.processid, ["empty prompt"], EXECUTOR_PRV)
            continue

        print(f"[executor] prompt: {prompt!r}")
        try:
            response = run_cycle(prompt)
        except Exception as e:
            response = f"(crash) {e}"

        client.close(process.processid, [response], EXECUTOR_PRV)
        print(f"[executor] → {response[:120]}")

    except Exception as e:
        print(f"[executor] ! error: {e}")
        time.sleep(2)
