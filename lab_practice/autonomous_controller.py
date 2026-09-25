"""
Step 2 — Autonomous heating controller (simulation).

The LLM closes the loop on its own:
    perceive (read_sensors)  ->  decide (LLM: ON/OFF)  ->  act (set_actuator)
Every perception and action goes through the guarded Level 7 tools, so each one
is validated (whitelist + reason) and written to the `decisions` audit log.

This is the core of the project. It runs entirely in simulation — no hardware.
When the Pi arrives, nothing in this file changes; only the sensor/actuator
behind BuildSim changes.

Prereqs (the simulated world + data pipeline must be running):
    docker compose up -d          # Mosquitto + TimescaleDB
    ./buildsim start --port 9090  # BuildSim  (or: cd buildingsim && make run)
    python level4_consumer.py     # MQTT -> TimescaleDB   (fills the DB)
    python level4_sensor.py       # sim + sensor (BuildSim + MQTT) + heater actuator

Then, in another terminal:
    python autonomous_controller.py
"""
import time
from openai import OpenAI

import level7_tools as tools          # the guarded tools (perceive + act + audit)

# ---------------- settings ----------------
AGENT_ID    = "auto-heater-A109"
ROOM        = "A109"
ACTUATOR_ID = "level4-heater-A109-state"   # whitelisted in level7_tools.ACTUATOR_RULES
SETPOINT    = 21.0
BAND        = 0.5                            # comfort half-band (+/- degrees)
PERIOD_S    = 5                             # one decision every 5 seconds

LLM_URL   = "http://carbon.eislab.se:8000/v1"
LLM_MODEL = "google/gemma-4-E4B-it"
llm = OpenAI(base_url=LLM_URL, api_key="not-needed")

SYSTEM = (
    "You are an autonomous heating controller for one room. "
    "You are given the current temperature, the setpoint and a comfort band. "
    "Decide the heater state to keep the room near the setpoint. "
    "Reply with EXACTLY one word: ON or OFF. No explanation."
)

# ---------------- 1. PERCEIVE (guarded read from the time-series DB) ----------------
def perceive():
    res = tools.call(AGENT_ID, "read_sensors",
                     {"room": ROOM, "sensor_types": ["temperature"], "last_seconds": 60})
    if not res.success:
        return None, res.error
    temp = res.data.get("sensors", {}).get("temperature")
    if not temp:
        return None, "no temperature data yet (is level4_sensor + level4_consumer running?)"
    return temp["current"], temp.get("trend", 0.0)

# ---------------- 2. DECIDE (the one LLM call) ----------------
def decide(temp, trend):
    user = (f"Room {ROOM} is {temp} C. Setpoint {SETPOINT} C (+/- {BAND}). "
            f"Recent trend {trend:+.2f} C. Heater ON or OFF?")
    resp = llm.chat.completions.create(
        model=LLM_MODEL, temperature=0.1,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user",   "content": user}])
    raw = resp.choices[0].message.content.strip()
    state = "on" if "ON" in raw.upper() else "off"
    return state, raw

# ---------------- 3. ACT (guarded actuation + audit) ----------------
def act(state, temp):
    reason = f"temp {temp}C vs setpoint {SETPOINT}C -> heater {state}"
    return tools.call(AGENT_ID, "set_actuator",
                      {"actuator_id": ACTUATOR_ID, "state": state, "reason": reason})

# ---------------- the loop ----------------
def main():
    print(f"Autonomous controller for {ROOM}: setpoint {SETPOINT}C, decision every "
          f"{PERIOD_S}s. Ctrl-C to stop.\n")
    while True:
        temp, extra = perceive()
        if temp is None:
            print(f"[perceive] {extra}")
            time.sleep(PERIOD_S)
            continue
        try:
            state, raw = decide(temp, extra)
        except Exception as e:
            print(f"[decide] LLM error: {e}")
            time.sleep(PERIOD_S)
            continue
        res = act(state, temp)
        status = "OK" if res.success else f"BLOCKED: {res.error}"
        print(f"[cycle] {temp:.2f}C  ->  LLM said {raw!r}  ->  heater {state.upper()}  ({status})")
        time.sleep(PERIOD_S)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.")
