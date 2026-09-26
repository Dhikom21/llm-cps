# Architecture Flow — What You Have Running Right Now

This is the specific stack you've built up through level 4 + TimescaleDB. Every component named is a real running process on your laptop. Print this and refer to it next time you stand at the whiteboard.

---

## 1. Process Map — Eight Real Things Running

```
HOST: your WSL2 / Windows laptop

┌────────────────────────────────────────────────────────────────────┐
│                                                                    │
│  ┌─────────────────────────┐    ┌─────────────────────────────┐    │
│  │ Windows host            │    │ WSL2 (Ubuntu)               │    │
│  │                         │    │                             │    │
│  │ Chrome / Firefox        │    │ ── Native processes ─────── │    │
│  │  • 3D viewer at         │    │   • make run  (BuildSim)    │    │
│  │    localhost:9090       │    │      ./bin/buildsim :9090   │    │
│  │                         │    │   • python level4_sensor.py │    │
│  │ (browser opens          │    │   • python level4_consumer  │    │
│  │  WebSocket to BuildSim) │    │   • python level5_agent     │    │
│  │                         │    │      (or level6 LLM)        │    │
│  └─────────────────────────┘    │                             │    │
│            ▲ │                  │ ── Docker containers ────── │    │
│            │ │ WS push          │   • d7065e_mosquitto :1883  │    │
│            │ ▼ + REST           │   • d7065e_timescaledb :5432│    │
│            via                  │                             │    │
│         localhost               │   (Docker desktop / engine  │    │
│         forwarding              │    bridges WSL ↔ host)      │    │
│                                 └─────────────────────────────┘    │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

Eight processes:

| # | Process | Where | Port | Role |
|---|---------|-------|------|------|
| 1 | BuildSim (`./bin/buildsim`) | WSL native | 9090 | building state + 3D viewer + WS |
| 2 | Browser (Chrome) | Windows | (opens 9090) | renders 3D viewer |
| 3 | `d7065e_mosquitto` | Docker | 1883 | MQTT broker |
| 4 | `d7065e_timescaledb` | Docker | 5432 | time-series store |
| 5 | `level4_sensor.py` | WSL Python | — | simulator + sensor + actuator threads |
| 6 | `level4_consumer.py` | WSL Python | — | MQTT → TimescaleDB bridge |
| 7 | `level5_agent_rule.py` (or `level6`) | WSL Python | — | perceive / reason / act loop |
| 8 | `curl` (or `make run` in another terminal) | WSL | — | manual command surface (optional) |

---

## 2. The Big Picture — Every Arrow in Your Stack

```
                                  ┌──────────────────┐
                                  │     Browser      │
                                  │  (Chrome on Win) │
                                  └────────┬─────────┘
                                           │
                          REST GETs        │  WebSocket /ws/{sid}
                          (bldg, equip,    │  pushed deltas
                           icons,          │  (highlight, version
                           POST session)   │   bumps, etc.)
                                           │
                              ▼            ▼
                         ┌──────────────────────────┐
                         │        BUILDSIM          │
                         │  Go binary, port 9090    │
                         │  in-memory store         │
                         │  state RESETS on restart │
                         └──┬──────────▲────▲───────┘
                            │          │    │
                  REST WS   │          │    │
                  pushes    │          │    │
                  to browser│  REST    │    │  REST
                            │  POST    │    │  PUT actuator state
                            │  equip + │    │  (the agent commands)
                            │  PUT     │    │
                            │  sensor  │    │
                            │  value   │    │
                            │          │    │
                            │          │    │   (also: GET equip,
                            │          │    │    GET graph/route)
                            │          │    │
        ┌───────────────────┴─────┐    │    │
        │                          │   │    │
        │   level4_sensor.py       │   │    │
        │   ┌──────────────────┐   │   │    │
        │   │  Simulator       │   │   │    │
        │   │  thread (ticks   │   │   │    │
        │   │  every 0.5 s)    │   │   │    │
        │   │  owns: temp,     │   │   │    │
        │   │  heater_on       │   │   │    │
        │   └────┬─────────────┘   │   │    │
        │        │ in-mem state    │   │    │
        │        ▼                 │   │    │
        │   ┌──────────────────┐   │   │    │
        │   │  Sensor thread   │───┼───┘    │
        │   │  reads sim,      │   │        │
        │   │  publishes       │   │        │
        │   └────┬─────────────┘   │        │
        │        │                 │        │
        │        │ MQTT publish    │        │
        │        ▼ topic:          │        │
        │   ┌────┴─────────────┐   │        │
        │   │  Actuator thread │───┼────────┘  (mirrors to BuildSim
        │   │  polls BuildSim, │   │            once it picks a state)
        │   │  applies to sim  │   │
        │   └──────────────────┘   │
        └──────────────────────────┘
                  │
                  │ MQTT
                  │ topic: sensors/level0/A109/temperature
                  │ payload: {ts, sensor_id, room, level, type, unit, value}
                  │
                  ▼
       ┌─────────────────────────┐
       │   d7065e_mosquitto      │
       │   MQTT broker           │
       │   port 1883             │
       │   QoS 1                 │
       └────────┬────────────────┘
                │
                │ subscribe sensors/#
                │ at-least-once delivery
                │
                ▼
       ┌─────────────────────────┐
       │  level4_consumer.py     │
       │  reads MQTT,            │
       │  inserts into Postgres  │
       └────────┬────────────────┘
                │
                │ INSERT INTO readings (...)
                │ via psycopg2 over TCP
                │
                ▼
       ┌──────────────────────────────────────────┐
       │      d7065e_timescaledb                  │
       │      Postgres + TimescaleDB extension    │
       │      port 5432, db = building            │
       │                                          │
       │      readings hypertable:                │
       │        ts, sensor_id, room, floor,       │
       │        type, unit, value                 │
       │                                          │
       │      ✓ supports concurrent readers       │
       │        (the consumer can keep writing    │
       │         while the agent + query script   │
       │         read in parallel)                │
       └────────▲─────────────────────────────────┘
                │
                │ SELECT value FROM readings
                │ WHERE room='A109' AND type='temperature'
                │   AND ts > now() - INTERVAL '10 seconds'
                │ ORDER BY ts ASC
                │
                │
       ┌────────┴────────────────┐
       │  level5_agent_rule.py   │      perceive
       │   (or level6 LLM agent) │  ─►  reason
       │                         │  ─►  act
       │                         │
       │  comfort band 20–23 °C  │      every 3 s (rule)
       │                         │      every 5 s (LLM)
       └────────┬────────────────┘
                │
                │ HTTP REST PUT /api/actuators/.../state
                │ body: {"state": "on"} or "off"
                │
                ▼
              BUILDSIM
                │
                │ (the actuator thread inside level4_sensor.py
                │  is polling BuildSim and notices the new state)
                ▼
            sim.heater_on = True
                │
                ▼
           sim.tick raises temp
                │
                ▼
           sensor reads new temp
                │
                └──────► back to MQTT, broker, DB, agent ...
                         the LOOP CLOSES
```

That's everything. Print this on A3 if you can.

---

## 3. Mapping to the Course's 8-Layer Model

| Layer | Component in your stack | Provided by |
|---|---|---|
| 1. **BuildSim** | `./bin/buildsim` on port 9090 | course |
| 2. **Sensor process** | sensor thread inside `level4_sensor.py` | you |
| 3. **Actuator process** | actuator thread inside `level4_sensor.py` | you |
| 4. **Physical simulator** | `Simulator` class inside `level4_sensor.py` | you |
| 5. **Message broker** | `d7065e_mosquitto` (Docker) | Eclipse Mosquitto |
| 6. **Storage** | `d7065e_timescaledb` (Docker) | Postgres + Timescale extension |
| 7. **AI agent** | `level5_agent_rule.py` (rule) or `level6_agent_llm.py` (LLM) | you |
| 8. **Dashboard** | the BuildSim 3D viewer in Chrome | course (via the session API) |

This is **exactly the Grade 4 architecture** from `lab-assignment/grading.md`. Every layer present, every layer in its own process boundary.

---

## 4. The Forward Flow — One Reading, End to End

We trace a single temperature reading from the moment the simulator ticks. Wall-clock times are typical for a laptop.

```
t=0.000s  Simulator thread:  Simulator.tick(dt=0.5)
                             ├─ gain = 3.0  (heater on)
                             ├─ loss = (T - 5) * 0.05
                             └─ temp += (gain - loss) * 0.5
                             new state: temp = 24.7

t=0.001s  (sensor thread is between cycles, sleeping)

t=1.000s  Sensor thread wakes:
            v = sim.temp + tiny_noise = 24.71

          [PUBLISH 1: REST to BuildSim]
            HTTP PUT http://localhost:9090
                /api/sensors/level4-temp-A109-val/value
            body: {"data_type":"text","value":"24.71"}
            ◄ 200 OK

          [PUBLISH 2: MQTT to Mosquitto]
            connect: tcp://localhost:1883  (already established)
            topic:   sensors/level0/A109/temperature
            payload: {"ts":1714225320.250,
                      "sensor_id":"level4-temp-A109-val",
                      "room":"A109","level":"level0",
                      "type":"temperature","unit":"C",
                      "value":24.71}
            QoS:     1  (at-least-once)

t=1.005s  BuildSim handler runs:
            in-memory store updated
            broadcasts on WS:  {"type":"equipment","version":N+1}

t=1.006s  Browser receives version bump:
            GET /api/equipment   ← refetches the whole list
            re-renders A109's thermometer with 24.71

t=1.007s  Mosquitto delivers MQTT message to subscriber
          ┌─ d7065e_mosquitto routes message to:
          └─ level4_consumer.py (subscribed to sensors/#)

t=1.012s  level4_consumer.py.on_message():
            INSERT INTO readings
              (ts, sensor_id, room, floor, type, unit, value)
            VALUES (to_timestamp(1714225320.250),
                    'level4-temp-A109-val',
                    'A109','level0','temperature','C',
                    24.71)
          via psycopg2 over TCP localhost:5432

t=1.020s  TimescaleDB:
            row inserted into chunk for "today"
            indexed on (room, type, ts DESC)
            rolling tail of the agent's view is now:
              [..., 24.55, 24.61, 24.66, 24.71]

t=1.0...  At some later point inside its 3-second cycle,
          level5_agent_rule.py runs perceive():
            SELECT value FROM readings
            WHERE room='A109' AND type='temperature'
                  AND ts > now() - INTERVAL '10 seconds'
            ORDER BY ts ASC

          rows → values [22.40, 22.55, 22.91, 23.18, 23.45,
                         23.81, 24.10, 24.40, 24.71]
          avg = 23.5  trend = +2.31 (10 s rise)

          reason() → temp > 23.0 → decision = "off"

          act():
            HTTP PUT http://localhost:9090
                /api/actuators/level4-heater-A109-state/state
            body: {"state":"off"}
            ◄ 200 OK

t=1.0...  BuildSim flips actuator state.
          Actuator thread inside level4_sensor.py polls BuildSim
          (every 0.5 s), notices state change, applies:
            sim.heater_on = False

t=2.000s  Next simulator tick:
            gain = 0.0 (heater off!)
            temp drifts back DOWN toward outdoor (5°C)

t=2.001s  Sensor cycle restarts. Loop closes.
```

This is the entire course in one trace. Every horizontal line is a real wire on your laptop right now.

---

## 5. The Backward Flow — Decision to Physical Effect

The forward flow is data. The backward flow is action. Let's trace what happens when the agent decides "turn off the heater" (or you fire `curl`).

```
agent decision:  "off"
   │
   │   HTTP PUT /api/actuators/level4-heater-A109-state/state
   │   body: {"state":"off"}
   │
   ▼
BuildSim handler:
   ├─ updates in-memory actuator state (off)
   ├─ broadcasts on WS:  {"type":"equipment","version":N+1}
   │      └─► browser refetches, redraws the radiator icon
   │          (no visible change yet — temperature lags by seconds)
   │
   └─ at this point the SIMULATOR doesn't know yet
      (BuildSim is just storage; it can't move physics)

actuator thread (running inside level4_sensor.py):
   │   loops every 0.5 s
   │   GET /api/equipment/level4-heater-A109
   │   sees state = "off" (different from last seen)
   │
   ▼
   sim.heater_on = False
   prints: "[actuator] state -> off"

   (the SIMULATOR is now in the new state)

next simulator tick (0–0.5 s later):
   │   gain = 0.0 (heater off)
   │   loss = (T - 5) * 0.05
   │   net = -0.05 * (T - 5)        (temperature falls)
   │
   ▼
   sim.temp drops by ~0.05 °C/s toward 5°C

next sensor cycle (~ 1 s after that):
   │   reads new sim.temp (slightly lower)
   │   publishes via REST to BuildSim AND MQTT to broker
   ▼
forward flow takes over again
```

Note the **hop count**: an agent decision touches BuildSim, then the actuator thread, then the simulator, then the sensor, then BuildSim again, then the consumer, then the database, before the agent's *next* perceive sees the effect. That's the closed-loop latency — typically 2–5 seconds in your stack. That delay is exactly why the agent runs at 3-second cycles, not 30 Hz.

---

## 6. The Five Real Wires in Your Setup

In the deep dive I called out five canonical seams. Here they are, instantiated in your laptop:

### Seam A — Simulator ↔ Sensor (and Actuator)

In your stack this is a **Python in-process function call** (because all three threads share `sim`). In a Grade 5 stack each would be its own container with an HTTP or MQTT seam between them. Currently:

```python
# in level4_sensor.py
sim = Simulator()
# sensor thread: v = sim.temp + noise
# actuator thread: sim.heater_on = (state == "on")
```

### Seam B — Sensor ↔ BuildSim (REST)

```
HTTP PUT  http://localhost:9090/api/sensors/level4-temp-A109-val/value
body: {"data_type":"text","value":"24.71"}
QoS: synchronous, sensor blocks for 200 OK
rate: 1 Hz
failure: try/except on requests.RequestException → log and continue
```

### Seam C — Sensor ↔ Broker (MQTT)

```
MQTT publish to mqtt://localhost:1883
topic: sensors/level0/A109/temperature
payload: {ts, sensor_id, room, level, type, unit, value}
QoS: 1 (at-least-once)
rate: 1 Hz
failure: paho's loop_start handles auto-reconnect
```

### Seam D — Agent ↔ Storage (SQL)

```
psycopg2 connection to postgres://postgres:postgres@localhost:5432/building
SELECT value FROM readings WHERE room=%s AND type='temperature'
       AND ts > now() - INTERVAL '10 seconds' ORDER BY ts ASC
rate: every 3 s (rule agent) or 5 s (LLM agent)
failure: connection re-establishes on next perceive
```

### Seam E — Agent ↔ Actuator (REST through BuildSim)

```
HTTP PUT http://localhost:9090/api/actuators/level4-heater-A109-state/state
body: {"state":"on"|"off"}
QoS: synchronous, agent blocks for 200 OK
rate: only when decision changes (low rate)
failure: try/except → log "actuator command failed" → next cycle retries
```

---

## 7. What's Stored Where

| State | Where | Survives restart? |
|---|---|---|
| Building geometry, floor plans | embedded in `buildsim` binary | yes (read-only) |
| Equipment registry, current sensor values, current actuator states | BuildSim's in-memory store | **NO** — wiped on `make run` restart |
| Browser session id, viewport, highlights, occupancy | BuildSim sessions store | **NO** |
| Physical simulator state (temp, heater_on) | `Simulator` object inside `level4_sensor.py` | **NO** — wiped if you Ctrl-C the script |
| MQTT in-flight messages | Mosquitto memory (default config) | NO with this config; Mosquitto can be persistent if you change `mosquitto.conf` |
| **Sensor reading history** | TimescaleDB `readings` hypertable (Docker volume `timescale_data`) | **YES** — survives `docker compose down`; only `docker compose down -v` wipes it |
| Agent decision logs | currently nowhere (you'd add this for Grade 4+) | NO |

This table is itself an exam question: *"if every component restarts, what state does your system rebuild from, and what's permanently lost?"*

---

## 8. What You're Missing for a Full Grade 4

Honestly inventory — what your current stack does NOT have, against `grading.md`:

| Grade 4 requirement | Status |
|---|---|
| Multiple sensor + actuator types working together | only temperature so far. Add CO2, humidity, occupancy → easy duplication. |
| Data pipeline with TSDB + SQL queries | ✓ done |
| ML/AI model evaluated with metrics | rule-based currently. The LLM (level 6) counts but you'd want metrics on its decisions. |
| Fault-injection tests | not yet — see `failures.md` |
| Integration tests | not yet |
| C4 diagrams | this document is essentially your container diagram |

So you're maybe 70% of the way to a Grade 4 demonstration on your laptop.

---

## 9. What Each Single Box Could Do Differently (Variation Catalogue)

For when a student team picks something else:

| Box | Yours | Common alternatives |
|---|---|---|
| BuildSim | `./bin/buildsim` | nothing — it's provided |
| Browser | Chrome | Firefox; **never Safari** (blocks WS over plain HTTP) |
| Broker | Mosquitto (MQTT 1883) | Kafka, RabbitMQ, Redis Streams, NATS |
| Storage | TimescaleDB | InfluxDB, DuckDB+Parquet+MinIO, ClickHouse, Postgres alone |
| Sensor process | Python thread | Go service, Rust, ESP32 firmware, replay from CSV |
| Simulator | Python ODE in process | physics-based separate service, replay-based, generative ML |
| Agent | Python rule / Ollama LLM | LangChain, LangGraph, AutoGen, MCP-based, RL policy |
| LLM | Ollama Gemma2:2b local | Llama 3, Mistral, GPT-4, Claude, Gemini |
| Dashboard | BuildSim session API | Grafana, custom React, Streamlit |
| Compose | Docker Compose | Kubernetes, Nomad, ColonyOS |

When you see a team using something different, ask *"why not X?"* using this table.

---

## 10. The Single Most Useful Diagram for Lab

If you only memorise one picture from this whole document, it's this:

```
SENSOR → BUILDSIM        →  BROWSER (live UI)
   │
   └→ BROKER → CONSUMER → TIMESCALEDB → AGENT → ACTUATOR → BUILDSIM
                                          │                   │
                                          └→ LLM             ▼
                                                       SIMULATOR
                                                            │
                                                            ▼
                                                       (physics)
```

That's the whole stack you have running, with every wire labelled. Everything else in this document is amplification of those lines.

---

## 11. Quick Health-Check Cheatsheet

When something stops working, check in this order:

```bash
# 1. Is BuildSim alive?
curl -s http://localhost:9090/api/building | head -c 100

# 2. Is Mosquitto alive?
docker compose ps mosquitto
mosquitto_sub -h localhost -t 'sensors/#' -C 3   # see 3 messages then quit

# 3. Is TimescaleDB alive?
python db.py

# 4. Is the consumer storing rows?
python query_readings.py    # row count should rise every few seconds

# 5. Is the sensor publishing?
docker logs d7065e_mosquitto --tail 20    # should see CONNECT and PUBLISH lines

# 6. Is the agent reading?
# run python level5_agent_rule.py — should print avg every 3 s
```

If any step shows nothing, you've isolated the failing component. Then read `failures.md` for the specific recovery steps.

---

## 12. One-Sentence Summary

> **You have built a real Grade-4 distributed CPS on your laptop: BuildSim plays the building, your Python script plays sensors+simulator+actuators, Mosquitto carries the data flow to TimescaleDB, an agent reads the database to perceive, and commands the actuator through BuildSim's REST API to close the loop.**

That's the whole course in one sentence. Every variation students bring you is a substitution of one of those nouns.
