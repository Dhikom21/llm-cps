# Level 7 — Failure Injection Scenarios

Run levels 4+5 (or 4+6) so the closed loop is humming. Then try each scenario one at a time. For each, write down what you observe and what you think *should* happen — that's the difference between Grade 3 and Grade 4 thinking.

Reset between scenarios with `Ctrl-C` everything and start again, or accept that state is dirty and observe accordingly.

---

## 7a. Kill the sensor process

```bash
# Ctrl-C the level4_sensor.py terminal
```

Observe: BuildSim's last value freezes; consumer stops getting MQTT; agent's DuckDB query returns increasingly stale rows; agent keeps acting on stale data.

What *should* happen: agent detects freshness > threshold and switches to the safety fallback. (Right now it doesn't — that's the gap students must close at Grade 4.)

---

## 7b. Kill the broker

```bash
docker compose stop mosquitto
```

Observe: sensor's MQTT publishes raise errors (silently swallowed in our code); BuildSim REST keeps working — the 3D viewer is unaffected; consumer disconnects; TSDB stops getting new rows.

```bash
docker compose start mosquitto
```

Does the sensor reconnect cleanly? Paho's `loop_start()` does auto-reconnect by default; verify with `mosquitto_sub -t 'sensors/#'` in another terminal.

---

## 7c. Restart BuildSim mid-run

```bash
# Ctrl-C the `make run` terminal, then re-run it
```

Observe: equipment registry is wiped. Sensor PUTs return 404. Browser WebSocket drops. Actuator state is lost.

What *should* happen: sensor process detects 404 and re-registers; browser auto-reconnects (it does); actuator process re-registers too. Right now your processes don't auto-reregister — note that gap, it's a real Grade 4 question.

---

## 7d. Kill the agent

```bash
# Ctrl-C the level5_agent_rule.py / level6_agent_llm.py terminal
```

Observe: sensors and consumer keep flowing; heater stays in its current state forever, regardless of room temperature.

What *should* happen: a separate watchdog (NOT the agent) enforces hard upper/lower bounds. Or the actuator times out the last command and reverts to a safe default.

---

## 7e. Make Ollama slow or kill it (level 6 only)

```bash
# stop Ollama or block port 11434 with the firewall
```

Observe: agent's HTTP request hits the 10 s timeout and falls back to the rule. Closed loop continues.

This is *the* demo of the lecturer's point in lecture 4: *"never trust the LLM as the only reasoning step; have a fallback."*

---

## 7f. Send corrupt sensor data

In a third terminal, while everything is running:

```bash
# Wrong data type
curl -X PUT http://localhost:9090/api/sensors/level4-temp-A109-val/value \
  -H "Content-Type: application/json" -d '{"data_type":"text","value":"NaN"}'
```

Or via MQTT:

```bash
mosquitto_pub -t 'sensors/level0/A109/temperature' -m '{"ts":1234,"value":"oops"}'
```

Observe: consumer crashes on the bad insert (because `oops` won't parse as DOUBLE)? Or silently swallows it? Either way, fix this — schema validation at the consumer is mandatory.

---

## 7g. Two agents fighting

Start `level5_agent_rule.py` AND `level6_agent_llm.py` simultaneously. They will issue contradicting commands.

Observe: heater toggles rapidly between on and off as both agents react to each other's actions.

What *should* happen: one of them must yield, OR a coordinator routes the conflict. This is exactly the Grade 5 multi-agent coordination problem.

---

## What this teaches you

Each scenario maps to a specific exam-floor question:

| Scenario | "What if..." question for whiteboard |
|---|---|
| 7a | What if a sensor crashes? |
| 7b | What if the broker is unavailable? |
| 7c | What if BuildSim restarts? |
| 7d | What if the agent crashes? |
| 7e | What if the LLM is slow or wrong? |
| 7f | What if a sensor sends bad data? |
| 7g | What if two agents conflict? |

If your students can't answer one of these for their own system, point them here.
