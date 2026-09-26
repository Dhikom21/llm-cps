# What is ColonyOS? — A Fresher's Guide

A plain-English standalone explainer. No prior knowledge of distributed systems assumed. Read it once and you should be able to explain what ColonyOS is, why anyone built it, and what kinds of things it can be coupled with.

---

## Part 1 — What ColonyOS is, in one sentence

**ColonyOS is a workload scheduler that decides when and where computer jobs run, and on which machine.**

It is *not* a programming language. It is *not* an AI. It is *not* a database. It is *not* a web server. It is a piece of software whose only job is to receive descriptions of work and dispatch them to the right computer.

A useful image: a courier service. A customer drops off an envelope at the depot. The depot looks at the address, finds a driver who is going that way, and hands the envelope over. The depot doesn't read the envelope, doesn't decide what to write inside, doesn't deliver it itself. It is the middleman that makes sure the envelope gets to the right place.

ColonyOS is that depot, for computer workloads.

---

## Part 2 — The four building blocks

Every concept in ColonyOS reduces to these four. Memorise them and the rest follows.

### 1. Colony

A **colony** is a tenant. A boundary. Everything inside one colony can collaborate; two colonies don't see each other. A university might have one colony for biology research, one for building control, one for student projects. Each is isolated from the others.

A useful image: a tenant on a floor in an office building. The building is shared infrastructure (corridors, lifts, electricity). Each tenant locks their own door, manages their own staff, and doesn't peek into the next tenant's office.

### 2. Executor

An **executor** is a worker. A piece of software that registers with a colony, says "here are the kinds of jobs I can do," and waits to be assigned work. Executors are pull-based — they walk to the job board, look for a posting that fits, and take it.

A useful image: a tradesperson at a labour exchange. Each morning the plumbers, electricians, and carpenters show up. The job board lists work. Each tradesperson picks the work that matches their skills.

Executors live on actual computers. One executor might run on your laptop and handle "edge" work. Another might run on the lab's GPU server and handle "training" work. A third might run in the cloud and handle "analytics." They all register with the same colony.

### 3. Function specification

A **function specification** (often shortened to "function spec" or "spec") is a job description. A JSON document that says:

- *What* to compute (a name like `highlight_room`, `train_model`, `send_alert`).
- *What arguments* to pass to it (a room ID, a dataset path, a message).
- *What kind of executor* should handle it (`edge`, `gpu`, `notification`).
- *How long* it's allowed to run before timing out.
- *How many retries* on failure.

A useful image: a work order on a clipboard. "Replace the leaky tap in room 304. Allow 90 minutes. If not done in two days, escalate."

### 4. Process

A **process** is a function spec that is currently in flight. The moment the broker matches a spec to an executor, a process is created. Processes have states: `waiting`, `running`, `successful`, `failed`. You can watch them in the dashboard or query them by ID.

A useful image: the work order has been picked up by the plumber and is now in progress. The clipboard has a sticker on it: "in progress — Janet" or "completed — 2:15 pm."

---

## Part 3 — How ColonyOS is different from things you may already know

People meet ColonyOS and ask "isn't this just X?" Five comparisons that help.

### vs. cron

`cron` is a scheduler that runs commands at fixed times on one machine. ColonyOS schedules work across *many* machines and matches it to capabilities.

A useful image: cron is a single alarm clock that rings at 9 a.m. ColonyOS is a dispatcher that knows which of fifty doctors is free and which patient needs which specialist.

### vs. Kubernetes

Kubernetes orchestrates long-running services (web servers, databases) inside a cluster. ColonyOS orchestrates short-running workloads ("compute this one thing and exit") across heterogeneous infrastructure — your laptop, a GPU server, a Raspberry Pi at the edge, and a cloud VM, all at once.

A useful image: Kubernetes is a building manager who keeps every shop in the mall open. ColonyOS is a city's logistics hub that routes packages between shops, warehouses, and customers.

### vs. AWS Lambda or Google Cloud Functions

Cloud function platforms run your code in a vendor-controlled environment. ColonyOS runs your code on *your* machines (or anyone's machines you have agreed to) under a unified scheduler. You own the executors.

A useful image: AWS Lambda is hailing a corporate Uber. ColonyOS is having your own dispatcher coordinate your own fleet of vehicles.

### vs. an LLM

An LLM thinks. ColonyOS schedules. An LLM is *content*. ColonyOS is *plumbing*. You can absolutely run an LLM inside a ColonyOS workload — but ColonyOS itself is not the LLM.

A useful image: the brain is the LLM. ColonyOS is the appointment book that decides which patient the brain sees next.

### vs. a message broker like RabbitMQ or Kafka

A message broker moves messages between programs. ColonyOS is also moves messages, but specifically structured *workload descriptions* — and it also matches them to executors, tracks lifecycle, retries, and reports outcomes.

A useful image: a message broker is a postal service. ColonyOS is a postal service plus a contractor registry plus a project-management tool.

---

## Part 4 — The architecture, in one picture

```
                  ┌────────────────────────────────┐
                  │     ColonyOS broker            │
                  │   (the central scheduler)      │
                  │                                │
                  │  receives function specs       │
                  │  matches to executors          │
                  │  tracks process lifecycle      │
                  │  authenticates signatures      │
                  └────┬────────────────────┬──────┘
                       │                    │
              executor │                    │ executor
              polls    │                    │ polls
              ("any    │                    │ ("any
              work?")  │                    │ work?")
                       ▼                    ▼
              ┌──────────────────┐  ┌──────────────────┐
              │  Edge executor   │  │  GPU executor    │
              │  (laptop / RPi)  │  │  (lab GPU srv)   │
              │                  │  │                  │
              │  knows how to:   │  │  knows how to:   │
              │   • read sensors │  │   • train models │
              │   • call REST    │  │   • run heavy AI │
              │   • highlight    │  │     inference    │
              │     rooms        │  │                  │
              └────────┬─────────┘  └──────────┬───────┘
                       │                       │
                       │ calls                 │ calls
                       ▼                       ▼
              ┌──────────────────┐  ┌──────────────────┐
              │  External system │  │  External system │
              │  (e.g. BuildSim, │  │  (e.g. PyTorch,  │
              │   an IoT device, │  │   Ollama,        │
              │   a database)    │  │   TensorFlow)    │
              └──────────────────┘  └──────────────────┘
```

Three things make up the system:

- **The broker** — one central process that holds the queue of work.
- **The executors** — one or many processes scattered across machines, each with a known set of capabilities.
- **The submitters** — anyone who can drop a function spec onto the broker. A developer with the CLI. A web frontend. Another executor producing follow-up work.

Submitters never call executors directly. Executors never call each other directly. Everything flows through the broker.

---

## Part 5 — The universal adapter pattern

The reason ColonyOS is useful for so many domains is that **anything with an API can be plugged in** by writing one executor. The executor is the **adapter** between ColonyOS's world and the world of whatever you want to control.

A useful image: a power adapter. The wall socket speaks one shape; your laptop's plug speaks another. The adapter knows both and translates between them. Replace the wall socket with "the broker's wire protocol" and the laptop plug with "an external system's API," and you have ColonyOS executors.

The pattern is always the same:

1. Your external system exists and has some kind of API (REST, MQTT, gRPC, a Python library, a shell command).
2. You write a small Python (or Go, etc.) process — the executor.
3. The executor polls the broker, claims work, translates the function spec's args into API calls, runs the work, reports the result.
4. The external system is unchanged. The broker is unchanged. Only the executor is new.

That's the entire integration recipe.

---

## Part 6 — Things ColonyOS can be coupled with

A non-exhaustive tour. Each example follows the same template: what the external system is, what kind of executor wraps it, what function spec invokes it, when you'd use it.

### Building control — BuildSim

**External system.** A 3D building simulator with a REST API for sensors, actuators, highlights, and routing.

**Executor.** A Python process that reads function spec args like `("room": "A2306", "color": "red")` and converts them into REST calls (`PUT /api/sessions/{sid}/highlights`).

**Function spec example.**
```json
{
  "funcname": "highlight_room",
  "args": ["A2306", "#ff0000"],
  "conditions": {"executortype": "buildsim-edge"}
}
```

**Use case.** Make a natural-language interface to a building. The chat box demo in this folder uses this exact pattern.

### ML model training — PyTorch on a GPU

**External system.** A GPU server with PyTorch installed.

**Executor.** A Python process with `executortype: "gpu"` that takes a dataset path and training hyperparameters, invokes a training script, uploads the model artifact when done.

**Function spec example.**
```json
{
  "funcname": "train_anomaly_model",
  "args": [{"dataset": "s3://gold/2026-04-28.parquet", "epochs": 50}],
  "conditions": {"executortype": "gpu"},
  "maxexectime": 1800
}
```

**Use case.** Nightly retraining of an anomaly model that the edge agent then hot-reloads. Scheduled via a ColonyOS cron rule that submits one spec every night at 3 a.m.

### LLM inference — Ollama or a hosted endpoint

**External system.** An LLM provider — local Ollama, a hosted vLLM server, OpenAI, Anthropic.

**Executor.** A Python process that takes a prompt, sends it to the LLM, returns the reply.

**Function spec example.**
```json
{
  "funcname": "llm_chat",
  "args": ["Summarise the last hour of sensor readings: ..."],
  "conditions": {"executortype": "llm"}
}
```

**Use case.** Centralise all LLM calls in your stack. Every prompt is logged, rate-limited, and routed to whichever endpoint is healthy. Multiple agents share the same LLM executors.

### IoT devices — real sensors and actuators

**External system.** Physical hardware on the network — ESP32 microcontrollers, Raspberry Pis, BACnet thermostats, Modbus controllers.

**Executor.** A process that speaks the device's protocol (MQTT for ESP32s, BACnet for HVAC controllers, Modbus for industrial PLCs).

**Function spec example.**
```json
{
  "funcname": "set_real_actuator",
  "args": ["modbus://hvac-floor-2/coil-3", "true"],
  "conditions": {"executortype": "iot-edge"}
}
```

**Use case.** Side by side with simulated devices, run the same agent on real hardware. The agent doesn't know whether it's commanding a simulator or a physical room.

### Robotics — ROS or Gazebo

**External system.** A robot running ROS, or a robotics simulator like Gazebo, Webots, or NVIDIA Isaac.

**Executor.** A ROS-aware process that translates function spec args into ROS service calls or action goals.

**Function spec example.**
```json
{
  "funcname": "move_robot_to",
  "args": ["robot-3", "5.0", "2.0", "1.57"],
  "conditions": {"executortype": "ros-bridge"}
}
```

**Use case.** A multi-robot fleet where ColonyOS schedules navigation goals, charging breaks, and task assignments across many robots.

### Cloud services — Slack, Gmail, S3, anything HTTP

**External system.** SaaS APIs — Slack, Gmail, GitHub, JIRA, S3, BigQuery, anything.

**Executor.** A thin process that holds an API token and translates function spec args into API calls.

**Function spec example.**
```json
{
  "funcname": "post_to_slack",
  "args": ["#alerts", "Fire detected in A2306. Sprinklers active."],
  "conditions": {"executortype": "slack"}
}
```

**Use case.** Decouple agents from credentials. The safety agent issues a `post_to_slack` spec; a dedicated Slack executor holds the token and does the actual post. Tokens never leave the executor.

### Scientific instruments — telescopes, microscopes, lab equipment

**External system.** An instrument's control software — a telescope's API, a spectrometer's USB driver, an X-ray diffractometer.

**Executor.** A process running on the machine attached to the instrument, exposing each instrument operation as a function name.

**Function spec example.**
```json
{
  "funcname": "telescope_capture",
  "args": ["NGC-1234", "60", "luminance"],
  "conditions": {"executortype": "telescope", "location": "kiruna"}
}
```

**Use case.** Remote scheduling of observation campaigns. A researcher submits jobs from home; the broker dispatches them when the instrument is available. This is one of ColonyOS's original use cases at LTU.

### Other simulators — EnergyPlus, OpenFOAM, SUMO, MATLAB

**External system.** A simulation engine like EnergyPlus (building energy), OpenFOAM (CFD), SUMO (traffic), or MATLAB.

**Executor.** A process that takes a simulation configuration, invokes the engine, returns the output dataset or summary statistics.

**Function spec example.**
```json
{
  "funcname": "run_energy_plus",
  "args": ["building-v3.idf", "stockholm.epw", "annual"],
  "conditions": {"executortype": "energyplus"}
}
```

**Use case.** Run hundreds of parallel simulation scenarios for design exploration. Each scenario is one function spec; the broker fans them out across all available compute.

### MCP servers — tools as a service

**External system.** A Model Context Protocol server that exposes some external system's API as MCP tools (e.g., the BuildSim MCP server in this course).

**Executor.** A process that holds an MCP client connection. On each `mcp_tool_call` function spec, it invokes the named tool on the named server.

**Function spec example.**
```json
{
  "funcname": "mcp_tool_call",
  "args": ["buildsim", "highlight_rooms", "[{\"room_id\":5,\"color\":\"#ff0000\"}]"],
  "conditions": {"executortype": "mcp-bridge"}
}
```

**Use case.** Use ColonyOS as a secure broker for tool calls. Every invocation is logged, rate-limited, and authorised per user. Multiple agents share the same MCP servers without each needing their own client.

---

## Part 7 — A day in the life — tracing one workload

To make all the above concrete, follow one workload end to end.

A weather researcher in Kiruna wants the local telescope to capture an image of galaxy NGC-1234 at sunset. She types in a web frontend: *"capture NGC-1234, 60-second exposure."*

```
Step 1.  Web frontend POSTs the request to a backend service.
Step 2.  Backend translates it into a ColonyOS function spec:
         { funcname: telescope_capture,
           args: ["NGC-1234", "60", "luminance"],
           conditions: { executortype: "telescope",
                         location: "kiruna" } }
Step 3.  Broker stores the spec. State: waiting.
Step 4.  At 7:42 pm, the telescope executor (running on the lab PC
         next to the telescope) polls the broker.
Step 5.  Broker hands the spec to the executor. State: running.
Step 6.  Executor calls the telescope's control API:
            telescope.set_target("NGC-1234")
            telescope.expose(seconds=60, filter="luminance")
         Waits 60 seconds.
         Reads the resulting FITS file.
Step 7.  Executor uploads the image to a shared storage location.
Step 8.  Executor calls client.close(processid, [image_url]).
         Broker marks the process successful.
Step 9.  Backend, which is polling, sees the success and reads the
         image URL.
Step 10. Web frontend displays the captured image.
```

Total time: ~75 seconds (60 of which is the camera exposure). The researcher waited, then saw her photo.

What ColonyOS did: routed the request to the right physical machine (Kiruna, not the Stockholm campus), tracked its lifecycle, captured the result, and made it queryable.

What ColonyOS did *not* do: capture the image, process it, decide what to photograph, or run the camera. Those are the telescope's and the executor's jobs.

---

## Part 8 — Identity and security — how ColonyOS knows who you are

ColonyOS does not use usernames and passwords. Instead it uses **cryptographic identity**.

Every entity in the system — a colony, an executor, a user — has its own **keypair**: a public ID anyone can see, and a private key that only the owner has. Every message sent to the broker is signed with the sender's private key. The broker verifies the signature against the public ID. No passwords. No session tokens. No shared secrets.

A useful image: imagine a bank that has no PINs and no passwords. Instead, every account holder has a personal stamp. When you send a deposit slip to the bank, you stamp it. The bank compares your stamp to the master stamp register. If it matches, the transaction is yours. The stamp can't be forged because each one is unique and produced from a private mould the holder keeps at home.

That's how ColonyOS authenticates everything. Lose your private key, and you've lost your "stamp." Anyone who has it can act as you. Hence keys are protected like passwords would be — in your shell environment, in a vault, never committed to a repo.

Three roles, three private keys:

- **Server admin key** — the master key of the whole broker. Used to create and destroy colonies.
- **Colony owner key** — the master key of one colony. Used to register executors and users in that colony.
- **Executor key** — used by an executor process to claim and complete work.
- **User key** (optional) — used by a non-admin individual to submit work to a colony.

You set the right key in the right place, and ColonyOS knows who's acting.

---

## Part 9 — Vocabulary reference

Every term in one place.

| Term | Definition |
|---|---|
| **ColonyOS** | An open-source workload scheduler for distributed compute |
| **Broker** | The central process that holds the queue and matches specs to executors |
| **Colony** | A tenant boundary; everything inside one is isolated from other colonies |
| **Executor** | A worker process that registers with a colony and pulls work matching its capabilities |
| **Executor type** | A string tag like `edge` or `gpu` that an executor declares; specs target this tag |
| **Function specification** | A JSON document describing one unit of work — what, how, where, deadlines |
| **Function name** | The function spec's `funcname` field; the executor uses it to dispatch to a handler |
| **Process** | A function spec currently in flight; has states waiting / running / successful / failed |
| **Cron** | A ColonyOS feature for submitting function specs on a schedule |
| **Generator** | A ColonyOS feature for emitting function specs in response to events |
| **Colony admin / owner** | The role authorised to register executors and users in a colony |
| **Server admin** | The role authorised to register and destroy colonies in the broker |
| **Keypair** | An Ed25519 public/private pair; identity in ColonyOS |
| **Public ID** | The hex string derived from a public key — the identity others see |
| **Private key** | The secret half of a keypair; signs every message |
| **Pull-based** | Executors actively ask the broker for work, rather than the broker pushing |
| **Capability matching** | The broker's rule of routing specs only to executors whose declared type matches |
| **Compute continuum** | The idea that workloads should flow seamlessly across edge, fog, cloud, and HPC |

---

## Part 10 — Summary in five sentences

1. ColonyOS is a workload scheduler — a central broker that receives JSON descriptions of work and dispatches them to registered executors based on declared capabilities.
2. The four concepts — colony (tenant), executor (worker), function specification (job), process (in-flight job) — describe the entire world; learn these four and the rest of the documentation makes sense.
3. Any external system with an API can be coupled to ColonyOS by writing one small executor that polls for work and translates function spec args into API calls — building simulators, GPU training, IoT devices, robots, LLMs, scientific instruments, cloud services, even other simulators.
4. ColonyOS uses cryptographic identity instead of passwords; every entity has a keypair, every message is signed, and the broker verifies signatures against public IDs.
5. ColonyOS is plumbing, not content; it routes and tracks work, but it never reasons, computes, or stores domain data itself — those are the responsibilities of the executors and the systems they wrap.

Hold these five sentences and you have the entire concept.
