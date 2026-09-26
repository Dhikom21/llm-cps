# D7065E Lab Practice — Runnable Examples

A hands-on sandbox for the lab leader. Every file here is runnable. Each level builds on the previous one. Read `D7065E_Practical_Playbook.md` (in the workspace outputs folder) for the full narrative.

## Setup once

```bash
# 1. Install Python deps
pip install -r requirements.txt

# 2. Start Mosquitto + TimescaleDB (only needed from level 4 onwards)
docker compose up -d

# Verify the DB is reachable
python db.py        # prints "PostgreSQL ..." and "readings rows: N"

# 3. Build & run BuildSim (separate terminal)
cd ../buildingsim
make run
```

Open Chrome at http://localhost:9090 — that's the 3D viewer.

## The ladder

| Level | File(s) | What it shows |
|---|---|---|
| 1 | (just curl + browser) | BuildSim alone — see `level1_curl.sh` |
| 2 | `level2_sensor.py` | One sensor process pushing values |
| 3 | `level3_sim_loop.py` | **Full closed loop** — sim + sensor + actuator in one file |
| 4 | `level4_sensor.py`, `level4_consumer.py` | MQTT broker + TimescaleDB time-series store |
| 5 | `level5_agent_rule.py` | Rule-based agent reading store, commanding actuator |
| 6 | `level6_agent_llm.py` | Local LLM (Ollama) replacing the rule |
| 7 | `level7_tools.py`, `level7_react_agent.py` | ReAct agent: Thought→Action→Observation, tool registry with validators + audit |
| 8 | `level8_multi_agent.py` | Three agents (safety/comfort/energy) + priority coordinator with hysteresis |
| — | `view_decisions.py` | Tail the audit log for any agent |
| 7 | `failures.md` | Failure-injection scenarios |

## Suggested run order

1. **Level 1.** Just open `http://localhost:9090`, then `bash level1_curl.sh`. Watch the icon appear.
2. **Level 2.** `python level2_sensor.py`. Click on room A109 in the viewer; values update every second.
3. **Level 3.** `python level3_sim_loop.py`. In another terminal, run the curl in the script's first print statement to toggle the heater. Watch temperature climb.
4. **Level 4.** Two terminals:
   - `python level4_consumer.py`
   - `python level4_sensor.py`
   Then query the store:
   - `python query_readings.py`
5. **Level 5.** Keep level 4 running. Add `python level5_agent_rule.py`. Closed-loop control kicks in.
6. **Level 6.** Install Ollama, `ollama pull gemma2:2b`, then swap in `python level6_agent_llm.py`.
7. **Level 7.** Read `failures.md` and try each.

## Cleanup

```bash
docker compose down              # stop Mosquitto + TimescaleDB (keeps data volume)
docker compose down -v           # ALSO wipe the TimescaleDB volume (fresh start)
# Ctrl-C BuildSim
```

## Inspecting the database manually

```bash
docker exec -it d7065e_timescaledb psql -U postgres -d building
\dt                              # list tables
SELECT count(*) FROM readings;   # row count
\q                               # quit
```

## File map

```
lab_practice/
├── README.md                   ← you are here
├── requirements.txt            ← Python deps
├── docker-compose.yml          ← Mosquitto + TimescaleDB
├── mosquitto.conf              ← broker config
├── db.py                       ← shared TimescaleDB connector + schema
├── level1_curl.sh              ← BuildSim API tour with curl
├── level2_sensor.py            ← simplest sensor process
├── level3_sim_loop.py          ← closed loop demo
├── level4_sensor.py            ← sensor + MQTT
├── level4_consumer.py          ← MQTT → TimescaleDB consumer
├── level5_agent_rule.py        ← rule-based agent
├── level6_agent_llm.py         ← LLM agent (Ollama)
├── query_readings.py           ← inspect the store
└── failures.md                 ← failure-injection scenarios
```
