# QUICKSTART — Level 1 → Level 7 (ReAct LLM) + checking decisions

Assumes a venv exists in this folder.
**Every new terminal:** `cd /mnt/c/Users/dhikom/Embedded/D7065E/lab_practice` then
`source venv/bin/activate` before running anything.

One-time deps: `pip install -r requirements.txt`

---

## Background services (start once, keep running)

```bash
# Terminal A — BuildSim (needed by ALL levels)
cd /mnt/c/Users/dhikom/Embedded/D7065E/buildingsim
make run
# → open http://localhost:9090  (3D viewer)

# Terminal B — infra (needed Level 4+)
cd /mnt/c/Users/dhikom/Embedded/D7065E/lab_practice
source venv/bin/activate
pip install -r requirements.txt   
docker compose up -d       # Mosquitto (MQTT) + TimescaleDB
python db.py               # verify DB reachable (prints version + row count)
```

---

## Level 1 — BuildSim API tour (curl)
```bash
bash level1_curl.sh
```
Watch: thermometer icon appears on A109, camera flies to it, then it's removed.

## Level 2 — one sensor process
```bash
python level2_sensor.py
```
Watch: click room A109 → value updates every second.
**Ctrl+C before Level 3.**

## Level 3 — closed loop (manual heater)
```bash
python level3_sim_loop.py
```
Toggle terminal:
```bash
curl -X PUT http://localhost:9090/api/actuators/level3-heater-A109-state/state \
     -H "Content-Type: application/json" -d '{"state":"on"}'
# ...watch A109 climb... then "off"
```
Watch: temp climbs toward ~65°C on, drifts to ~5°C off.
(Optional: `python latency_probe.py` — poll latency experiment.)
**Ctrl+C before Level 4.**

## Level 4 — MQTT + TimescaleDB pipeline
```bash
python level4_consumer.py     # Terminal C  (MQTT -> DB)
python level4_sensor.py       # Terminal D  (sim + sensor -> REST + MQTT)
python query_readings.py      # check: rows accumulating
```
**Keep C + D running for every level below.**

## Level 5 — rule-based agent
```bash
python level5_agent_rule.py   # Terminal E
```
Watch: heater flips on/off, temp oscillates inside the comfort band.
**Ctrl+C before Level 6.**

## Level 6 — LLM agent (remote LTU GPU)
```bash
# connect FortiClient VPN first (reaches carbon.eislab.se:8000)
curl -s http://carbon.eislab.se:8000/v1/models | head -c 200 ; echo   # reachability
python level6_agent_llm.py    # Terminal E
```
Watch: same loop, but the LLM makes the on/off decision.
**Ctrl+C before Level 7.**

## Level 7 — ReAct agent + tools (local Ollama)
```bash
ollama pull llama3.2:3b       # once (gemma2:2b also works, weaker)
ollama list                   # confirm model present + Ollama serving
python level7_react_agent.py  # Terminal E
```
Watch: explicit Thought -> Action -> Observation cycles, validated tools with
guardrails, audit trail. Falls back to a rule if the LLM is unavailable.

---

## Check the decisions (any time)
```bash
python view_decisions.py            # last 30 (Thought/Action/Observation/guardrail/fallback/final)
python view_decisions.py 100        # last 100
python view_decisions.py 30 safety  # filter by agent id substring
python query_readings.py            # sensor readings + per-sensor summary
```

Inspect the DB directly:
```bash
docker exec -it d7065e_timescaledb psql -U postgres -d building
\dt                                 # tables: readings, decisions
SELECT count(*) FROM decisions;
\q
```

---

## THE ONE RULE
Only **one** agent (Level 5 OR 6 OR 7) commands the heater at a time.
**Ctrl+C the previous agent before starting the next.**
Keep BuildSim + Docker + Level 4 consumer/sensor running underneath all of them.

## Cleanup
```bash
# Ctrl+C each Python script (they self-clean their BuildSim equipment)
docker compose down       # stop Mosquitto + TimescaleDB (keeps data)
docker compose down -v    # ALSO wipe DB volume (fresh start)
# Ctrl+C BuildSim
```

## Dependency ladder
```
L1  BuildSim
L2  + requests
L3  + threads (sim + sensor + actuator)
L4  + Docker (Mosquitto + TimescaleDB), paho-mqtt, psycopg2
L5  + rule agent (reads DB -> commands actuator)
L6  + remote LLM (carbon.eislab.se:8000, VPN)
L7  + local Ollama (llama3.2:3b) + tools/guardrails/audit
```
