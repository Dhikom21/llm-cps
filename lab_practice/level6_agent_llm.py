"""
Level 6 — LLM-based agent (TimescaleDB version).

Same scaffolding as level 5, but the rule is replaced by a remote LLM
running on the LTU EIS Lab GPU server (vLLM, OpenAI-compatible API).
The agent gets recent A109 temperature stats, a simple prompt, and a
JSON-only response constraint.

Pre-requisites:
  - The level 4 stack running (BuildSim, Mosquitto, TimescaleDB,
    level4_consumer.py, level4_sensor.py).
  - Network access to carbon.eislab.se:8000 (LTU network or VPN).

Run:
    python level6_agent_llm.py
"""
import json
import signal
import sys
import time

import requests

import db

BASE      = "http://localhost:9090"
LLM_URL   = "http://carbon.eislab.se:8000/v1/chat/completions"  # vLLM, OpenAI-compatible
MODEL     = "google/gemma-4-E4B-it"
TEMP      = 0.1              # near-deterministic for a safety decision
ROOM      = "A109"
ACT_STATE = "level4-heater-A109-state"
PERIOD_S  = 5.0
TIMEOUT_S = 10.0             # hard timeout — DO NOT remove

SYSTEM = """You are a building HVAC agent for room A109.
Your job: decide whether to turn the heater on, off, or keep its current state.
Comfort band: 20-23 C.
You receive recent temperature statistics and must respond with JSON only.
Never explain outside the JSON. Schema:
  {"action": "on" | "off" | "keep", "reason": "<short text>"}
"""

# Local fallback rule, used if the LLM fails. NEVER remove this — it is
# the safety guardrail discussed in lecture 4.
def fallback_rule(avg):
    if avg is None: return ("keep", "no data")
    if avg < 20.0:  return ("on",  "fallback: below comfort band")
    if avg > 23.0:  return ("off", "fallback: above comfort band")
    return ("keep", "fallback: in band")

PERCEIVE_SQL = """
    SELECT value FROM readings
    WHERE room = %s AND type = 'temperature'
          AND ts > now() - INTERVAL '30 seconds'
    ORDER BY ts ASC
"""

conn = db.connect()
last_command = None

def perceive():
    with conn.cursor() as cur:
        cur.execute(PERCEIVE_SQL, [ROOM])
        rows = cur.fetchall()
    if not rows:
        return None, None, 0
    values = [r[0] for r in rows]
    avg = sum(values) / len(values)
    trend = values[-1] - values[0] if len(values) >= 2 else 0.0
    return avg, trend, len(values)

def llm_reason(avg, trend, n):
    user = (f"Room {ROOM} temperature avg over last 30 s = {avg:.2f} C, "
            f"trend = {trend:+.2f} C, samples = {n}. Decide.")
    try:
        r = requests.post(LLM_URL,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user",   "content": user},
                ],
                "temperature": TEMP,
                "response_format": {"type": "json_object"},
                "stream": False,
            },
            headers={"Authorization": "Bearer not-needed",
                     "Content-Type":  "application/json"},
            timeout=TIMEOUT_S)
        if r.status_code != 200:
            print(f"  LLM HTTP {r.status_code} ({r.text[:120]}); using fallback")
            return fallback_rule(avg)
        # OpenAI-compatible response shape: choices[0].message.content
        raw = r.json()["choices"][0]["message"]["content"]
        # tolerate fenced JSON or leading prose
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").split("\n", 1)[1].rsplit("```", 1)[0]
        if not raw.startswith("{"):
            s, e = raw.find("{"), raw.rfind("}")
            if s != -1 and e != -1:
                raw = raw[s : e + 1]
        d = json.loads(raw)
        if d.get("action") not in ("on", "off", "keep"):
            print(f"  LLM bad action {d!r}; using fallback")
            return fallback_rule(avg)
        return d["action"], d.get("reason", "")
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"  LLM call failed ({e!r}); using fallback")
        return fallback_rule(avg)

def act(action, reason):
    global last_command
    if action == "keep" or action == last_command:
        return
    try:
        requests.put(
            f"{BASE}/api/actuators/{ACT_STATE}/state",
            json={"state": action},
            timeout=2.0)
        last_command = action
        print(f"  -> commanded heater {action!r}  reason: {reason}")
    except requests.RequestException as e:
        print(f"  ! actuator command failed: {e}")

def cleanup(*_):
    print("\nstopping agent.")
    try: conn.close()
    except Exception: pass
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

print(f"LLM agent running. model={MODEL} @ {LLM_URL}, "
      f"cycle={PERIOD_S}s, timeout={TIMEOUT_S}s. Ctrl-C to stop.")

while True:
    avg, trend, n = perceive()
    if avg is None:
        print(f"[{time.strftime('%H:%M:%S')}] no recent data; sleeping")
        time.sleep(PERIOD_S); continue

    action, reason = llm_reason(avg, trend, n)
    print(f"[{time.strftime('%H:%M:%S')}] avg={avg:5.2f}  trend={trend:+.2f}"
          f"  -> action={action}  ({reason})")
    act(action, reason)
    time.sleep(PERIOD_S)
