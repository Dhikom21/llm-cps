# Running ALL D7065E Lab Scenarios (Levels 1–8)

A practical, terminal-by-terminal runbook. The infrastructure grows as you climb:

| Levels | Needs |
|--------|-------|
| 1–3 | BuildSim + Python (`requests`) |
| 4–5 | + Docker: Mosquitto (MQTT) + TimescaleDB |
| 6 | + a remote LLM (LTU `carbon.eislab.se:8000`, via VPN) |
| 7–8 | + a local LLM (Ollama) |

All paths assume WSL: `/mnt/c/Users/dhikom/D7065E/...`

---

## 0. One-time setup

```bash
# Python deps (in the lab_practice venv)
cd /mnt/c/Users/dhikom/D7065E/lab_practice
source venv/bin/activate
pip install -r requirements.txt        # requests, paho-mqtt, psycopg2-binary
```

Docker Desktop must be running (with WSL integration enabled) before Level 4.
Keep a note of which terminal is which — you'll juggle several.

---

## Background services (start these, leave them running)

### Terminal A — BuildSim (needed for ALL levels)
```bash
cd /mnt/c/Users/dhikom/D7065E/buildingsim
make run                               # serves on http://localhost:9090
```
Open **http://localhost:9090** in Chrome — that's the 3D viewer.

### Terminal B — Docker infra (needed from Level 4 on)
```bash
cd /mnt/c/Users/dhikom/D7065E/lab_practice
docker compose up -d                   # Mosquitto :1883 + TimescaleDB :5432
python db.py                           # verify DB: prints "PostgreSQL ..." + row count
```

---

## Level 1 — BuildSim API tour (curl only)

**Needs:** BuildSim.
```bash
cd /mnt/c/Users/dhikom/D7065E/lab_practice
bash level1_curl.sh
```
**Watch:** a thermometer icon appears on room A109, its value cycles, the camera
flies to A109 and highlights it red, then the demo sensor is removed.

---

## Level 2 — one sensor process

**Needs:** BuildSim.
```bash
source venv/bin/activate
python level2_sensor.py
```
**Watch:** click room **A109** in the viewer — temperature updates every second
(a random walk). `Ctrl-C` cleans up.

---

## Level 3 — full closed loop (sim + sensor + actuator)

**Needs:** BuildSim.
```bash
python level3_sim_loop.py
```
In a **toggle terminal**, turn the heater on / off:
```bash
curl -X PUT http://localhost:9090/api/actuators/level3-heater-A109-state/state \
     -H "Content-Type: application/json" -d '{"state":"on"}'
# ... watch A109 climb, then:
curl -X PUT http://localhost:9090/api/actuators/level3-heater-A109-state/state \
     -H "Content-Type: application/json" -d '{"state":"off"}'
```
**Watch:** temperature climbs toward ~65°C with heater on, drifts toward 5°C off.
`Ctrl-C` cleans up.

**Bonus experiment:** `python latency_probe.py` measures actuator poll latency
(network ~a few ms vs poll-gap up to 500 ms).

---

## Level 4 — MQTT pipeline + TimescaleDB store

**Needs:** BuildSim + Docker infra (Terminal B).
Level 4's sensor runs its own sim + actuator inline, so you do NOT need Level 3.

### Terminal C — consumer (MQTT → TimescaleDB)
```bash
source venv/bin/activate
python level4_consumer.py
```
### Terminal D — sensor (BuildSim REST + MQTT publish)
```bash
source venv/bin/activate
python level4_sensor.py
```
### Inspect the stored data (any terminal)
```bash
python query_readings.py               # per-sensor summary, row counts
```
**Watch:** readings accumulate in TimescaleDB while the viewer updates live.
Two writes per reading: REST → UI (present), MQTT → DB (past).

---

## Level 5 — rule-based agent

**Needs:** everything from Level 4 running (BuildSim, infra, consumer, sensor).
### Terminal E — agent
```bash
source venv/bin/activate
python level5_agent_rule.py
```
**Watch:** the agent reads recent temps from TimescaleDB and toggles the heater to
hold A109 inside the comfort band — temperature **oscillates** between the limits.

---

## Level 6 — LLM agent (remote LTU GPU)

**Needs:** Level 4 stack running **+ network to `carbon.eislab.se:8000`**
(on LTU network or via **FortiClient VPN**). The rule from Level 5 is replaced by
a remote vLLM model (OpenAI-compatible API).
```bash
source venv/bin/activate
python level6_agent_llm.py
```
**Watch:** same closed-loop control, but the decision comes from the LLM
(fed recent temperature stats, returns JSON on/off).

> Note: this file targets the LTU server, not local Ollama. If you want to use
> your own self-hosted LLM (RTX 5090), point the script's base URL at your
> OpenAI-compatible endpoint instead.

---

## Level 7 — ReAct agent + tool catalogue

**Needs:** Level 4 stack + **local Ollama** with a tool-calling model.
```bash
# install Ollama, then:
ollama pull llama3.2:3b                # gemma2:2b also works (weaker tool calls)
```
### Terminal E — ReAct agent
```bash
source venv/bin/activate
python level7_react_agent.py
```
**Watch:** explicit **Thought → Action → Observation** cycles; the agent calls
narrow, validated tools (from `level7_tools.py`) with guardrails + an audit trail.
If Ollama is down/invalid, it **falls back to a rule** (graceful degradation).
### View the audit log (any terminal)
```bash
python view_decisions.py               # last 30 decisions
python view_decisions.py 100 safety    # filter
```

---

## Level 8 — multi-agent coordination

**Needs:** Level 4 stack + local Ollama (as Level 7).
```bash
source venv/bin/activate
python level8_multi_agent.py
```
**Watch:** three agents propose actions each cycle —
**Safety** (priority 1000, forces heater OFF above 30°C, always wins),
**Comfort** (priority 20, holds the 20–23°C band),
**Energy** (priority 10, prefers OFF when temp > 22.5°C) —
and a priority coordinator (with hysteresis) picks ONE command per cycle.
Inspect decisions with `python view_decisions.py`.

---

## Bonus — failure injection (great for your failure study)

```bash
cat failures.md          # scenarios: BuildSim down, MQTT down, bad LLM output, etc.
```
Try each while an agent runs and watch how it copes (or doesn't) via
`view_decisions.py`. This directly feeds the "agent failure modes" research idea.

---

## Cleanup

```bash
# Ctrl-C each Python script (they self-clean their BuildSim equipment)
docker compose down          # stop Mosquitto + TimescaleDB (keeps data)
docker compose down -v       # ALSO wipe the DB volume (fresh start)
# Ctrl-C BuildSim in Terminal A
```

## Inspect the database manually
```bash
docker exec -it d7065e_timescaledb psql -U postgres -d building
\dt                          # list tables (readings, decisions)
SELECT count(*) FROM readings;
\q
```

---

### Dependency ladder at a glance
```
L1  BuildSim
L2  BuildSim + requests
L3  BuildSim + requests (threads)
L4  + Docker (Mosquitto + TimescaleDB) + paho-mqtt + psycopg2
L5  + rule agent (reads DB, commands actuator)
L6  + remote LLM (carbon.eislab.se:8000, VPN)
L7  + local Ollama (llama3.2:3b) + tools/audit
L8  + multi-agent coordinator
```
