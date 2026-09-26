# Every Possible Connection in the BuildSim Architecture

Exhaustive inventory of every arrow that can appear in a student's diagram. Use this when you're standing at a whiteboard and want to know *what could be there* vs. *what is there*.

Each connection is listed with: **from → to**, the wire it uses, the payload, and what it's for.

---

## Section 1 — The Cast of Possible Actors

Eighteen things can appear as a box on a whiteboard. Most teams use 8–12 of them.

| # | Actor | Provided? | Typical count |
|---|-------|-----------|----------------|
| 1 | BuildSim | Yes | 1 |
| 2 | Browser (3D viewer) | Yes | 1+ |
| 3 | Physical simulator | Student | 1 |
| 4 | Sensor process | Student | N (one per sensor) |
| 5 | Actuator process | Student | N (one per actuator) |
| 6 | Message broker (MQTT/Kafka/Redis) | Student | 1 |
| 7 | Stream consumer / ingester | Student | 1+ |
| 8 | Time-series DB (Influx/Timescale/DuckDB) | Student | 1 |
| 9 | Object storage / data lake (MinIO + Parquet) | Student | 1 |
| 10 | ETL / batch processor | Student | scheduled |
| 11 | AI agent | Student | 1+ |
| 12 | Coordinator (multi-agent) | Student | 1 (Grade 5) |
| 13 | LLM (Ollama / cloud API) | Mixed | 1 |
| 14 | MCP server (wraps BuildSim as tools) | Provided in `buildingsim/mcp/` | 1 (optional) |
| 15 | Dashboard process | Student | 1 |
| 16 | Audit / decision log | Student | 1 |
| 17 | Metrics + Grafana | Student | 1 (optional) |
| 18 | ColonyOS / external orchestrator | Optional, Grade 5 | 1 |

Plus optional: real hardware (Raspberry Pi, ESP32), external datasets, weather APIs.

---

## Section 2 — Bird's-Eye View of All Possible Arrows

```
                                                          ┌─────────────┐
                                                          │   Browser   │
                                                          │ (3D viewer) │
                                                          └──────▲──────┘
                                                                 │ WS push
                                                                 │ + REST
                                                                 │
  ┌──────────────────────────────────────────────────────────────┴──────┐
  │                                                                      │
  │                              BuildSim                                │
  │       (REST API on :9090, WebSocket /ws/{sid}, in-memory)           │
  │                                                                      │
  └─▲───▲───▲────▲──────────▲────────────▲──────────────▲──────▲────────┘
    │   │   │    │          │            │              │      │
    │REST│   │REST│PUT       │REST       │REST          │REST  │REST
    │push│   │push│actuator  │read       │PUT session   │MCP   │REST
    │val │   │reg │state     │equipment  │state         │tools │
    │    │   │    │          │           │              │      │
   ┌┴┐  ┌┴───┴─┐ ┌┴─────┐  ┌┴──────┐  ┌┴────────┐    ┌┴────┐ ┌┴───┐
   │S│  │ Reg  │ │ Act  │  │ Agent │  │Dashboard│    │ MCP │ │ ML │
   │e│  │ flow │ │proc  │  │       │  │ proc    │    │ srv │ │trn │
   │n│  └──────┘ └──┬───┘  └──┬────┘  └─────────┘    └──┬──┘ └────┘
   │s│              │         │                          │
   │o│              │         ├──────────────────────────┘
   │r│              │         │                tool calls (MCP)
   │ │  ┌───────────┴─────────┴──────────────────┐
   │p│  │     Message Broker (MQTT/Kafka/...)     │
   │r│  └───────────────┬──────────────────────────┘
   │o│                  │
   │c│                  ├──► Stream consumer ──► TSDB / Parquet
   │ │                  │                              │
   └┬┘                  ├──► alert stream              │
    │                   │                              ▼
    │ publish to MQTT   ├──◄ actuator commands     ┌────────┐
    └──────────────────┘    from agent             │  ETL   │
                                                   │ batch  │
   ┌──────────────────────┐                        └───┬────┘
   │ Physical simulator   │◄──── actuator updates       │
   │ (ODEs, replay,       │                             ▼
   │  hybrid)             │                       Feature store
   │                      │──── true state ──► sensor reads
   └──────────────────────┘
                                                 ┌─────────────┐
                            ┌────────────────────┤ Audit Log   │
                            │  decision events   └─────────────┘
                            │
                       ┌────┴───┐
                       │ Agent  │──► LLM API (Ollama / cloud)
                       └────────┘
```

You don't need to draw this for students — it's for you, so you can spot what's missing in their diagrams.

---

## Section 3 — Connections from BuildSim's Point of View

BuildSim is the most-connected box. Here is everything that can talk to it, and how.

### 3.1. BuildSim ← Sensor process (Seam B in the deep dive)

**Equipment registration (once, on startup):**
```
POST /api/equipment
  body: {id, name, type, category, level, room, status}
POST /api/equipment/{id}/sensors
  body: {id, name, type, data_type, unit, value}
POST /api/equipment/notify
  body: (none)
```

**Continuous reading publish:**
```
PUT /api/sensors/{sensor_id}/value
  body: {"data_type":"text","value":"21.55"}
        or {"data_type":"binary","binary_value":true}
```

Sync HTTP, push from sensor. Failure: retry with backoff; if persistent, push to broker only and reconnect to BuildSim later.

### 3.2. BuildSim ← Actuator process

**Registration:**
```
POST /api/equipment
POST /api/equipment/{id}/actuators
  body: {id, name, type, state}
POST /api/equipment/notify
```

**Update actuator state when it actually applies the command:**
```
PUT /api/actuators/{actuator_id}/state
  body: {"state":"on"}
```

**Optional poll (if using pull pattern instead of MQTT command):**
```
GET /api/equipment/{equipment_id}
  → check actuators[].state, compare to last_seen, apply on change
```

### 3.3. BuildSim ← AI Agent

Agents can talk to BuildSim directly (instead of through an actuator process), three ways:

**Read current state of any sensor / actuator:**
```
GET /api/equipment            ?level=&room=&type=&category=
GET /api/equipment/{id}
```

**Direct actuator command (skipping actuator process):**
```
PUT /api/actuators/{id}/state
  body: {"state":"on"}
```
*Caveat:* this updates BuildSim's state but does **not** affect the physical simulator unless the actuator process is also watching. So this works only if you've made BuildSim's state the source of truth for the simulator too. Most teams don't.

**Compute evacuation route:**
```
GET /api/graph/route?from_name=A2306&to_name=A109&type=walkable
GET /api/graph/route?from_name=A2306&to_name=A109&level=level0&type=walkable
```

**Get the navigation graph (rare; usually agents use the route endpoint):**
```
GET /api/graph?level=level1&type=walkable
GET /api/building/cross-floor-edges
```

### 3.4. BuildSim ← Dashboard process

The dashboard pushes session state for visualisation. Five separate endpoints:

```
PUT /api/sessions/{sid}/viewport     {room, zoom, mode, floor}
PUT /api/sessions/{sid}/highlights   [{room_id, color, opacity}, ...]
PUT /api/sessions/{sid}/occupancy    {room_id: {persons:[], aliens:[]}}
PUT /api/sessions/{sid}/route        {path:[...], distance}
PUT /api/sessions/{sid}/coverage     [{room, radius, color, opacity}, ...]
```

Plus session lifecycle:
```
GET    /api/sessions               → list active sessions
POST   /api/sessions               → create new (rare; usually dashboard
                                      attaches to the browser's session)
GET    /api/sessions/{sid}         → get session state
DELETE /api/sessions/{sid}         → end session
```

### 3.5. BuildSim ← Browser

The 3D viewer auto-fetches everything it needs on page load:

```
GET /api/building
GET /api/building/floors/{level0|level1|level2}
GET /api/equipment
GET /api/icons/{name}.svg          (one per icon type, 48 icons)
GET /api/config
POST /api/sessions                 (creates the browser's own session)
```

Then it opens a WebSocket and stays subscribed:
```
WS /ws/{session_id}        ← the only server-to-client push channel
```

### 3.6. BuildSim ← MCP server

The provided MCP server in `buildingsim/mcp/` wraps BuildSim's REST as tools (`read_sensors`, `set_actuator`, `highlight_rooms`, `find_route`, `set_coverage`, ...). Internally it just makes the same REST calls listed above, but exposes them through the Model Context Protocol so any MCP-capable LLM (Claude Desktop, Cursor, custom agents) can drive BuildSim natively.

```
LLM client ─── MCP/JSON-RPC ──► MCP server ─── REST ──► BuildSim
```

### 3.7. BuildSim → Browser (the only WebSocket)

The server pushes five message types over the per-session WebSocket:

```
{"type":"viewport",   "data":{...}}        ← full state pushed
{"type":"highlights", "data":[...]}        ← full state pushed
{"type":"route",      "data":{...}}        ← full state pushed
{"type":"coverage",   "data":[...]}        ← full state pushed
{"type":"occupancy",  "version":N}         ← version-only; browser refetches
{"type":"equipment",  "version":M}         ← version-only; browser refetches
```

The version-bump pattern is for high-cardinality state — the browser fetches the full list on its own schedule rather than the server broadcasting MB-scale equipment lists to every connected session.

---

## Section 4 — Connections that *don't* go through BuildSim

These are the seams between student-built components. BuildSim is uninvolved.

### 4.1. Sensor process ↔ Physical simulator

How the sensor gets the "true" value:

| Pattern | Wire | Used when |
|---|---|---|
| Same process, function call | in-memory | level 3 demo, single-process simulators |
| Shared file / SQLite | filesystem | quick hacks |
| HTTP `GET /state?room=A109&type=temperature` | REST | when sim is its own service |
| MQTT subscribe `sim/state/{room}/{type}` | pub/sub | when sim publishes ground truth |
| ZeroMQ / Unix socket | IPC | high-rate (vibration sensors) |

The deep-dive's Seam A. Often hidden in diagrams when sim and sensor are the same Python process.

### 4.2. Actuator process ↔ Physical simulator

Mirror of 4.1: the actuator must apply the command to the simulator's state.

```
agent → actuator process → physical sim:
    sim.set_actuator(room="A2306", type="sprinkler", state="on")
```

If you skip this, BuildSim shows "sprinkler on" but the smoke keeps rising. The single most common bug at the lab.

### 4.3. Sensor → Broker

The data-pipeline branch. Independent of BuildSim REST, parallel to it.

**MQTT topic conventions:**
```
sensors/{level}/{room}/{type}             ← canonical hierarchy
sensors/level0/A109/temperature
sensors/level1/A2306/smoke
sensors/+/+/co2                           ← wildcard subscribe
sensors/#                                 ← all-sensors subscribe
```

**Payload (the standard schema):**
```json
{
  "ts": "2026-04-27T13:42:00.250Z",
  "sensor_id": "smoke-A2306-val",
  "room": "A2306",
  "level": "level1",
  "type": "smoke",
  "unit": "fraction",
  "value": 0.052
}
```

**QoS:** 1 (at-least-once) is the default; consumer must dedupe.

### 4.4. Broker → Stream consumer → Storage

The consumer subscribes (`sensors/#`) and writes to the time-series store. Two storage flavours:

**Direct insert:**
```
stream consumer ─── INSERT INTO readings(...) ──► TimescaleDB / InfluxDB
```

**Bronze Parquet write:**
```
stream consumer ─── append batch ──► s3://bronze/{date}/{room}/readings.parquet
```

In the lab this is usually one process writing both: an in-memory buffer that flushes to TimescaleDB every read and to Parquet every minute.

### 4.5. Stream consumer → derived event topic

Stream processors often emit derived events back into the broker for other consumers:

```
broker subscribes:  sensors/#
broker publishes:   alerts/anomaly                ← when score > threshold
                    alerts/staleness/{sensor_id}  ← when no reading in N s
                    derived/occupancy/{room}      ← computed from CO2 trend
```

These are first-class arrows. Multiple downstream consumers (agent, dashboard, alerting) subscribe.

### 4.6. ETL job ↔ Storage (the medallion arrows)

```
bronze (raw Parquet)  ──► [ETL] ──► silver (cleaned)
silver                ──► [ETL] ──► gold (features)
gold                  ──► [ML training] ──► model artifact
```

Wires:
- ETL reads/writes Parquet over S3 protocol (MinIO).
- DuckDB or pandas does the transformations in-process.
- Apache Airflow / cron / a hand-rolled scheduler triggers the runs.

### 4.7. Agent ↔ Storage (the read side)

Two patterns, mutually exclusive:

**Pull / SQL:**
```
agent ─── SELECT ... FROM readings WHERE ts > now() - '5m'
       ──► TSDB  (Influx HTTP API / Postgres / DuckDB)
```

**Push / subscribe:**
```
broker ─── sensors/# ──► agent's in-memory rolling window
```

In our practice playbook we used the SQL/pull pattern (level 5 reading from `readings.duckdb`).

### 4.8. Agent ↔ Actuator (the command path — Seam E)

Two patterns:

**Direct REST through BuildSim** (simpler):
```
agent ─── PUT /api/actuators/{id}/state ──► BuildSim
                                                  │
                                       (actuator proc polls and mirrors)
```

**Command via broker** (more decoupled):
```
agent ─── publish actuators/{id}/cmd ──► broker
                                            │
                                            ├──► actuator process
                                            │      ├─► applies to sim
                                            │      └─► PUT to BuildSim
                                            │
                                            └──► audit logger
```

The broker pattern lets you queue, throttle, log, and fan out to redundant actuators. The REST pattern is fine for Grade 3.

**Actuator process publishes back:**
```
actuator process ─── publish actuators/{id}/state ──► broker
                  body: {state, ts, applied_by, latency_ms}
```

This is the ack/observation channel — agents and dashboards can subscribe to confirm commands actually applied.

### 4.9. Agent ↔ LLM

```
agent ─── HTTP POST /api/chat ──► Ollama (local, port 11434)
                              ──► Anthropic / OpenAI / Gemini (cloud)
```

JSON body with `messages`, `model`, `format`, `stream`, `tools`. Response is JSON with the LLM's text or tool-calls.

**Critical sub-arrows when tool-calling:**
```
agent ─── tool call (LLM emits) ───► tool executor
tool executor ─── REST / MQTT ──► BuildSim or actuator
tool executor ─── result ───► agent's next LLM call
```

The agent runs a small loop: send context → receive tool call → execute → feed result back → repeat until LLM emits "no more tools."

### 4.10. Agent ↔ MCP server (alternative tool path)

When using MCP instead of inline tool definitions:
```
LLM client (agent) ─── MCP JSON-RPC ──► MCP server ─── REST ──► BuildSim
```

The MCP server hides the BuildSim API behind a tool catalogue. The agent doesn't know it's calling BuildSim — it calls `set_actuator` and the MCP server handles the REST.

### 4.11. Agent ↔ Audit log

```
agent ─── append decision record ──► audit log
   body: {ts, agent_id, perceived_state, reasoning,
          tool_calls, action_taken, outcome}
```

Wire options: append to Parquet file, INSERT into a `decisions` table, MQTT publish to `audit/decisions`, or write to syslog. Required for Grade 4 (you need the log to evaluate the agent later).

### 4.12. Multi-agent ↔ Coordinator (Grade 5)

```
              ┌─► safety agent  ──┐
sensors/store ┼─► comfort agent ──┤
              ├─► energy agent  ──┤─► Coordinator ──► actuators
              └─► security agent ─┘
```

**Coordinator wire patterns:**

| Pattern | Wire | Behaviour |
|---|---|---|
| Priority arbitration | shared topic + per-agent priority field | safety always wins |
| Auction | `actuators/{id}/bids` + coordinator timer | highest bid wins |
| Negotiation | per-agent ↔ peer messaging | agents converge via dialog |
| Voting | each agent publishes a vote; coordinator counts | majority decides |

Multi-agent coordination is the hardest design conversation in the course. Most teams skip it unless aiming for Grade 5.

### 4.13. Dashboard ↔ everything else

Dashboards typically subscribe to multiple sources at once:

```
dashboard ───► BuildSim REST          (push session state)
          ◄─── broker (sensors/#)     (live readings for charts)
          ◄─── broker (alerts/)       (anomaly notifications)
          ◄─── broker (audit/)        (agent decisions)
          ◄─── TSDB                   (historical charts)
```

If using Grafana, Grafana itself has its own connections:
```
Grafana ─── Influx / Prometheus / Postgres datasources ──► TSDB
browser ─── HTTP ──► Grafana
```

### 4.14. ML training pipeline

Cold-path arrows that aren't part of the live loop:

```
gold dataset ──► training process ──► model artifact (pickle / ONNX / TF)
model artifact ──► model store (S3, filesystem, registry)
agent (on startup or on hot-reload) ──► fetch model artifact
```

In a Grade 5 system, training runs on a GPU server, pushed via ColonyOS:
```
ColonyOS broker ◄── training job submission
ColonyOS broker ──► GPU executor (the actual training)
GPU executor ──► model store
ColonyOS broker ──► edge executor (deploy new model)
edge executor ──► restart agent with new model
```

### 4.15. Metrics / observability (good practice)

Every long-running process exposes metrics for scraping:

```
sensor proc ─── /metrics endpoint    ──◄ scraped by ──── Prometheus
broker      ─── /metrics             ──◄                  │
agent       ─── /metrics             ──◄                  │
                                                          ▼
                                                       Grafana
                                                       (dashboards)
```

Plus log aggregation:
```
all containers ─── stdout ──► Docker logs ──► Loki ──► Grafana
```

### 4.16. External hardware (optional)

For the optional A-Building real-hardware integration:

```
ESP32 (real sensor) ─── WiFi/MQTT ──► broker (same topics as simulated)
ESP32 (real actuator) ◄── WiFi/MQTT ──◄ broker
```

The whole point of this option is that real hardware uses the *same* topics as simulated devices. The agent doesn't know the difference. That's the Grade 5 architecture-quality conversation.

---

## Section 5 — All Connections in One Big Table

Read this row by row. The columns are the wire type and what's actually carried.

| # | From | To | Wire | Payload / endpoint | Required for | Notes |
|---|------|-----|------|---------------------|---|---|
| 1 | sensor proc | BuildSim | HTTP REST | `POST /api/equipment` | every grade | startup registration |
| 2 | sensor proc | BuildSim | HTTP REST | `POST /api/equipment/{id}/sensors` | every grade | attach sensor |
| 3 | sensor proc | BuildSim | HTTP REST | `PUT /api/sensors/{id}/value` | every grade | continuous publish |
| 4 | sensor proc | BuildSim | HTTP REST | `POST /api/equipment/notify` | every grade | wake browser |
| 5 | sensor proc | broker | MQTT publish | `sensors/{lvl}/{rm}/{type}` | Grade 4+ | data pipeline |
| 6 | sensor proc | physical sim | function call / IPC | `sim.read(room, type)` | every grade | ground truth |
| 7 | actuator proc | BuildSim | HTTP REST | `POST /api/equipment` | every grade | registration |
| 8 | actuator proc | BuildSim | HTTP REST | `POST /api/equipment/{id}/actuators` | every grade | attach actuator |
| 9 | actuator proc | BuildSim | HTTP REST | `PUT /api/actuators/{id}/state` | every grade | mirror applied state |
| 10 | actuator proc | BuildSim | HTTP REST | `GET /api/equipment/{id}` | poll-pattern only | listen for commands |
| 11 | actuator proc | broker | MQTT subscribe | `actuators/{id}/cmd` | broker-pattern only | listen for commands |
| 12 | actuator proc | broker | MQTT publish | `actuators/{id}/state` | Grade 4+ | ack/state-change |
| 13 | actuator proc | physical sim | function call / IPC | `sim.set_actuator(...)` | every grade | apply physically |
| 14 | physical sim | external dataset | filesystem read | weather.csv, occupancy.parquet | replay-based sims | exogenous inputs |
| 15 | broker | stream consumer | MQTT subscribe | `sensors/#` | Grade 4+ | ingest |
| 16 | stream consumer | TSDB | SQL / line protocol | `INSERT INTO readings(...)` | Grade 4+ | history |
| 17 | stream consumer | object storage | S3 write | `s3://bronze/.../readings.parquet` | Grade 4+ if data lake | bronze layer |
| 18 | stream consumer | broker | MQTT publish | `alerts/anomaly` | Grade 4+ | derived events |
| 19 | ETL job | object storage | S3 read | bronze parquet | Grade 4+ | clean data |
| 20 | ETL job | object storage | S3 write | silver / gold parquet | Grade 4+ | medallion |
| 21 | ML training | object storage | S3 read | gold features | Grade 4+ | training data |
| 22 | ML training | model store | filesystem write | model.pkl / model.onnx | Grade 4+ | artifact |
| 23 | agent | TSDB | SQL | `SELECT ... WHERE ts > now()-5m` | every grade (Pull) | perceive |
| 24 | agent | broker | MQTT subscribe | `sensors/#`, `alerts/#` | optional | low-latency perceive |
| 25 | agent | model store | filesystem read | model.pkl | Grade 4+ | inference |
| 26 | agent | LLM | HTTP POST | `/api/chat` to Ollama | Grade 4 LLM track | reasoning |
| 27 | agent | LLM | HTTPS POST | Anthropic / OpenAI | optional | reasoning |
| 28 | agent | actuator proc | MQTT publish | `actuators/{id}/cmd` | broker pattern | command |
| 29 | agent | BuildSim | HTTP REST | `PUT /api/actuators/{id}/state` | direct pattern | command |
| 30 | agent | BuildSim | HTTP REST | `GET /api/equipment` | optional | sanity-check state |
| 31 | agent | BuildSim | HTTP REST | `GET /api/graph/route` | use-case dependent | evacuation route |
| 32 | agent | audit log | append / SQL / MQTT | decision record | Grade 4+ | provenance |
| 33 | agent ↔ agent | broker / coordinator | MQTT / JSON | bids / votes / messages | Grade 5 | multi-agent |
| 34 | coordinator | actuator | MQTT / REST | priority-resolved command | Grade 5 | conflict resolution |
| 35 | dashboard | BuildSim | HTTP REST | `PUT /api/sessions/{sid}/highlights` | every grade | colour rooms |
| 36 | dashboard | BuildSim | HTTP REST | `PUT /api/sessions/{sid}/coverage` | optional | risk zones |
| 37 | dashboard | BuildSim | HTTP REST | `PUT /api/sessions/{sid}/occupancy` | optional | people icons |
| 38 | dashboard | BuildSim | HTTP REST | `PUT /api/sessions/{sid}/route` | optional | path display |
| 39 | dashboard | BuildSim | HTTP REST | `PUT /api/sessions/{sid}/viewport` | optional | camera |
| 40 | dashboard | broker | MQTT subscribe | `sensors/#`, `alerts/#` | optional | live charts |
| 41 | dashboard | TSDB | SQL | charts queries | optional | history charts |
| 42 | browser | BuildSim | HTTP REST | `GET /api/building`, equipment, etc | every grade | initial fetch |
| 43 | browser | BuildSim | HTTP POST | `POST /api/sessions` | every grade | own session |
| 44 | BuildSim | browser | WebSocket | `{type, data\|version}` | every grade | the only push |
| 45 | MCP server | BuildSim | HTTP REST | wraps endpoints as tools | optional | MCP track |
| 46 | LLM client | MCP server | MCP JSON-RPC | `tools/call` | optional | agent via MCP |
| 47 | metrics | Prometheus | HTTP scrape | `/metrics` per process | optional | observability |
| 48 | Grafana | Prometheus / TSDB | SQL / PromQL | dashboards | optional | charts |
| 49 | Browser | Grafana | HTTP | view dashboards | optional | UI |
| 50 | real ESP32 sensor | broker | MQTT | same topics as virtual | Grade 5 option | hardware integration |
| 51 | real actuator | broker | MQTT | same topics | Grade 5 option | hardware integration |
| 52 | ColonyOS executor | ColonyOS broker | gRPC | function specs | Grade 5 option | compute continuum |
| 53 | ColonyOS broker | edge executor | gRPC | dispatched workload | Grade 5 option | edge deployment |
| 54 | ColonyOS broker | GPU executor | gRPC | training job | Grade 5 option | training |

---

## Section 6 — Connection Matrix (compact reference)

Rows = source, columns = destination. Cell shows the dominant wire. Empty = no direct connection (they talk via someone else).

|  | BuildSim | broker | TSDB | sim | act-proc | agent | LLM | dashb | browser |
|---|---|---|---|---|---|---|---|---|---|
| sensor-proc | REST | MQTT-pub | — | call | — | — | — | — | — |
| broker | — | — | — | — | MQTT | (sub) | — | (sub) | — |
| consumer | — | (sub) | SQL | — | — | — | — | — | — |
| TSDB | — | — | — | — | — | (read) | — | (read) | — |
| sim | — | — | — | — | — | — | — | — | — |
| act-proc | REST | MQTT-pub | — | call | — | — | — | — | — |
| agent | REST | MQTT-pub | SQL | — | MQTT-pub | — | HTTP | — | — |
| LLM | — | — | — | — | — | (resp) | — | — | — |
| MCP-srv | REST | — | — | — | — | (resp) | — | — | — |
| dashboard | REST | — | SQL | — | — | — | — | — | — |
| BuildSim | — | — | — | — | — | — | — | — | WS |
| browser | REST | — | — | — | — | — | — | — | — |

Read it like: the sensor process talks to BuildSim over REST, to the broker via MQTT publish, and to the simulator via in-process function call. Nothing else.

---

## Section 7 — How Many of These Will Any One Team Have?

| Grade target | Typical connection count |
|---|---|
| Grade 3 | 8–12 |
| Grade 4 | 18–25 |
| Grade 5 | 30+ |

Grade 3 teams often have only:
1. sensor → BuildSim (push)
2. agent → BuildSim (poll)
3. agent → BuildSim (actuator command)
4. browser ← BuildSim (WS)
5. sim ↔ sensor + actuator (in-process)

Grade 5 teams add: broker, TSDB, data lake, ETL, ML training, model store, multi-agent coordinator, audit log, metrics, ColonyOS, optionally real hardware.

When a team's diagram is missing a connection from this inventory **that their use case requires**, that's your interrogation lever.

---

## Section 8 — What's *Not* a Connection (common confusions)

These often get drawn but shouldn't:

- **Browser → broker (MQTT direct).** Browsers don't speak MQTT natively. They speak WebSocket. If a team draws this arrow, they need MQTT-over-WebSocket bridging — possible (Mosquitto supports it on port 9001) but rarely needed in this course.
- **Agent → browser.** Agents don't talk to browsers directly. Agents push session state to BuildSim; BuildSim pushes to the browser.
- **BuildSim → physical simulator.** BuildSim doesn't know the simulator exists. The actuator process bridges.
- **Sensor → agent direct.** Possible but bypasses the data pipeline. Caps the team at Grade 3.
- **Dashboard → simulator.** No reason to connect; the simulator's truth is reflected in the sensor data.

When you see one of these on a whiteboard, that's a sign of confused mental model.

---

## Section 9 — One-Line Summary

> **Eighteen possible actors, fifty-four possible arrows, every grade is a subset.** Start every architecture review by checking which actors are present and which of the canonical arrows between them are drawn — then ask why the missing ones are missing.
