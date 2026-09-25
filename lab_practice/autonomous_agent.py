"""
autonomous_agent.py — the ReAct version of autonomous_controller.py.

autonomous_controller.py made ONE call and got back a single word (ON/OFF).
This version lets the LLM use MANY tools over several 'hops' in a loop:

    think  ->  ask for a tool?  ->  run it (guarded)  ->  feed the result back  ->  think again
    ... until the model gives a plain-text final answer with no tool call.

That is the ReAct loop. It uses native tool-calling, so it needs a model that
supports it: a local Ollama (e.g. llama3.2:3b) or a vLLM started with
--enable-auto-tool-choice. The LTU Gemma server does NOT support tool-calling,
so point LLM_URL at a local Ollama for this one.

Prereqs (simulated world + pipeline running):
    BuildSim + level4_consumer.py + level4_sensor.py
Then:
    ollama pull llama3.2:3b
    python autonomous_agent.py
"""
import json
import time

from openai import OpenAI

import db
import level7_tools as tools

AGENT_ID = "react-A109"
ROOM     = "A109"
HEATER   = "level4-heater-A109-state"
BAND_LO, BAND_HI = 20.0, 23.0
CYCLE_S  = 8
MAX_HOPS = 4                     # loop guard: max tool calls per cycle

LLM_URL   = "http://localhost:11434/v1"   # local Ollama (tool-calling capable)
LLM_MODEL = "llama3.2:3b"
llm = OpenAI(base_url=LLM_URL, api_key="ollama")

SYSTEM = f"""You are the autonomous HVAC agent for room {ROOM}.
Goal: keep the temperature within {BAND_LO}-{BAND_HI} C.
Tools you may call: read_sensors; set_actuator (heater id={HEATER}, state 'on'|'off',
reason >=5 chars); create_alert; query_history.
Each cycle: call read_sensors first; change the heater only if needed (with a reason);
raise create_alert on anomalies (no data / stuck sensor / fast rise); then reply with a
short plain-text summary and NO tool call. Be conservative: prefer keeping the current
state over flipping repeatedly."""


# ---------- fallback rule (graceful degradation) ----------
def fallback():
    s = tools.call(AGENT_ID, "read_sensors",
                   {"room": ROOM, "sensor_types": ["temperature"], "last_seconds": 30})
    t = s.data.get("sensors", {}).get("temperature", {}) if s.success else {}
    avg = t.get("mean")
    if avg is None:
        tools.call(AGENT_ID, "create_alert",
                   {"severity": "warning", "message": "fallback: no data", "room": ROOM})
        return
    if avg < BAND_LO:
        tools.call(AGENT_ID, "set_actuator",
                   {"actuator_id": HEATER, "state": "on",
                    "reason": f"fallback: avg {avg:.1f} below {BAND_LO}"})
    elif avg > BAND_HI:
        tools.call(AGENT_ID, "set_actuator",
                   {"actuator_id": HEATER, "state": "off",
                    "reason": f"fallback: avg {avg:.1f} above {BAND_HI}"})


# ---------- one ReAct cycle ----------
def cycle(n):
    print(f"\n=== cycle {n} === {time.strftime('%H:%M:%S')}")

    # PERCEIVE: take a snapshot and seed the notebook with it
    snap = tools.call(AGENT_ID, "read_sensors",
                      {"room": ROOM, "sensor_types": ["temperature"], "last_seconds": 30})
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",
         "content": f"Snapshot just taken:\n{json.dumps(snap.data)}\nDecide what to do."},
    ]

    # REASON + ACT, up to MAX_HOPS times
    for hop in range(MAX_HOPS):
        try:
            resp = llm.chat.completions.create(
                model=LLM_MODEL, temperature=0.1,
                messages=messages, tools=tools.SCHEMAS, tool_choice="auto")
        except Exception as e:
            print(f"  [llm] failed: {e} -> using fallback rule")
            fallback()
            return

        msg = resp.choices[0].message
        if msg.content:
            print(f"  [thought] {msg.content.strip()}")

        # No tool requested -> the model's final answer -> cycle done
        if not msg.tool_calls:
            print(f"  [final] {(msg.content or '').strip()}")
            return

        # Staple the model's request into the notebook so it sees it next turn
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
        })

        # ACT each requested tool, OBSERVE, and feed the result back
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            print(f"  [action] {name}({json.dumps(args)[:100]})")
            result = tools.call(AGENT_ID, name, args)          # guarded + audited
            print(f"  [observ] success={result.success} {str(result.data)[:90]}")
            messages.append({
                "role": "tool", "tool_call_id": tc.id,
                "content": json.dumps(result.to_dict()),
            })

    print("  [warn] reached MAX_HOPS — stopping this cycle")


if __name__ == "__main__":
    db.ensure_schema()
    print(f"ReAct agent {AGENT_ID}: model={LLM_MODEL}, cycle={CYCLE_S}s. Ctrl-C to stop.")
    n = 0
    try:
        while True:
            n += 1
            try:
                cycle(n)
            except Exception as e:
                print(f"  [error] cycle crashed: {e}")
            time.sleep(CYCLE_S)
    except KeyboardInterrupt:
        print("\nstopped.")
