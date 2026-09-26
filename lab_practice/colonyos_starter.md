# ColonyOS Starter — Understanding the Pieces Before Integrating

A hands-on path from zero to a working ColonyOS + BuildSim setup, designed for learning the concepts step by step. MCP integration is the last stage; the earlier stages keep things minimal so the ColonyOS model stays the focus.

---

## Part 1 — What ColonyOS Is, in Plain Terms

ColonyOS is a **workload scheduler** for distributed compute. It is *not* an LLM, *not* a message broker, and *not* a database. Its single job is: "given a description of work to do, find a machine that can do it, dispatch the work, and track the result."

A useful image: a postman. The postman takes a letter (a workload), looks at the address (the requirements), and delivers it to the right house (the executor). The postman doesn't read the letter or do the work inside it.

The job of a ColonyOS developer is to:
1. Write a small JSON document describing what should be computed and what resources it needs.
2. Hand that document to the broker.
3. Watch the dashboard while an executor picks it up, runs it, and reports back.

The work itself is normal Python (or any language) that you write. ColonyOS just decides when and where it runs.

---

## Part 2 — The Four Actors

Four concepts make up the ColonyOS world. Memorise these and the rest of the documentation becomes readable.

### Colony

A boundary. Everything inside one colony can talk to everything else inside that colony, but two different colonies are isolated. Think of a colony as a tenant in a multi-tenant building. Your university could have one colony for the lab, another for an industry partner, and the two don't see each other.

### Executor

A worker that runs the actual code. Each executor registers with a colony, declares its capabilities (`gpu`, `edge`, `cpu`, `iot`, or any tag you invent), and waits for work. Executors are pull-based — they ask the broker "anything for me?" rather than the broker pushing.

A useful image: a worker who walks to the job board, sees a posting that matches their skills, takes the slip, and starts work.

### Function specification

A JSON document describing one unit of work. Fields include:
- the **function name** to run (a string the executor knows how to interpret)
- the **arguments** to pass
- the **conditions** (what kind of executor is needed)
- the **max execution time** (a deadline)
- the **max retries** (how many times to retry on failure)

A useful image: a work order on the job board. "Paint this fence. Must be done by Friday. Needs someone with a ladder."

### Process

A running instance of a function specification. Once the broker matches a function spec to an executor, a process is created. Processes have states: `waiting`, `running`, `successful`, `failed`. The dashboard shows them in a table.

A useful image: the actual job in progress, with a job number, a worker assigned, and a clock running.

These four make up the model. Everything else — the dashboard, the CLI, the SDKs — is tooling for managing them.

---

## Part 3 — Getting ColonyOS Running

ColonyOS ships with a `docker-compose.yml` that brings up the broker and the dashboard.

### Step 1 — Clone the repository

```bash
cd ~
git clone https://github.com/colonyos/colonies.git
cd colonies
```

### Step 2 — Start the broker and dashboard

```bash
cd docker
docker compose up -d
docker compose ps
```

You should see at least:

- `colonies-server` (the broker, on port 50080)
- `colonies-postgres` (the broker's metadata store)
- `colonies-dashboard` (the web UI, typically on port 3000)

Open `http://localhost:3000` in your browser. You'll see the dashboard with no colonies yet.

If a port is already taken (5432 is often grabbed by your existing TimescaleDB), edit the compose file's port mappings to use a different host port for ColonyOS's postgres, or stop your other services first.

### Step 3 — Install the `colonies` CLI

The CLI is the easiest way to interact with the broker.

```bash
# from inside the colonies repository
go install ./cmd/colonies

# verify
colonies version
```

You'll need Go installed (which you already have). The binary lands in `~/go/bin/colonies`. Add `~/go/bin` to your `PATH` if it isn't already.

### Step 4 — Configure the CLI

The CLI reads connection details from environment variables. Add these to your shell (or to `~/.bashrc` to persist):

```bash
export COLONIES_SERVER_HOST=localhost
export COLONIES_SERVER_PORT=50080
export COLONIES_SERVER_TLS=false
```

Verify the CLI can reach the broker:

```bash
colonies server status
```

You should see a JSON blob with version and uptime info.

---

## Part 4 — Your First Colony and Your First Executor

ColonyOS uses Ed25519 cryptographic identity instead of usernames and passwords. You generate a keypair; the public key becomes your identity.

### Step 1 — Generate a colony identity

```bash
# generate a private key for the colony
colonies key generate
```

You'll see something like:

```
ID: 5e0a8c2b7f...
PrvKey: ABCDEF1234...
```

The ID is the colony's address; the private key is the credential to administer it. **Save the private key somewhere safe** — it cannot be regenerated.

### Step 2 — Register the colony

```bash
colonies colony add --colonyid <id-from-step-1> --name d7065e
```

Refresh the dashboard. The colony `d7065e` appears.

### Step 3 — Generate an executor identity

```bash
colonies key generate
```

Save this second keypair. The executor will use it.

### Step 4 — Register the executor

```bash
export COLONIES_COLONY_ID=<colony-id>
export COLONIES_COLONY_PRVKEY=<colony-private-key>

colonies executor add \
  --executorid <executor-id-from-step-3> \
  --name laptop-edge \
  --type generic \
  --colonyname d7065e
```

The dashboard now shows one executor under the colony, but its status is `pending` because the broker hasn't seen it actually connect.

---

## Part 5 — Your First Function Spec

The "hello world" of ColonyOS: a function spec that prints a message.

### Step 1 — Write `hello.json`

```json
{
  "conditions": {
    "colonyname": "d7065e",
    "executortype": "generic"
  },
  "funcname": "echo",
  "args": ["hello from d7065e"],
  "maxwaittime": 60,
  "maxexectime": 30,
  "maxretries": 0
}
```

### Step 2 — Submit it

```bash
export COLONIES_PRVKEY=<your-executor-private-key>
colonies function submit --spec hello.json
```

The CLI prints the process ID and the function spec returns to `waiting` state — because there is no executor actually polling yet. Look at the dashboard: a new process appears, status `waiting`, no executor assigned.

This is the moment to internalise the model. The broker accepted the work. It is now sitting in a queue, waiting for someone capable to claim it.

### Step 3 — Write a tiny executor that claims it

The simplest possible executor is a Python script that polls the broker, claims work, runs it, and reports the result.

Save this as `simple_executor.py`:

```python
"""
Simplest possible ColonyOS executor in Python.
Claims any 'echo' function spec and prints its arguments.
"""
import os
import time

from pycolonies import colonies_client

EXECUTOR_ID  = os.environ["EXECUTOR_ID"]
EXECUTOR_PRV = os.environ["EXECUTOR_PRVKEY"]
COLONY_NAME  = os.environ["COLONIES_COLONY_NAME"]

client = colonies_client()

print(f"Executor {EXECUTOR_ID[:12]}... starting")

while True:
    try:
        # Claim work, blocking up to 5 seconds
        process = client.assign(COLONY_NAME, 5, EXECUTOR_PRV)
        if process is None:
            continue

        spec = process.spec
        print(f"Got process {process.processid[:12]} funcname={spec.funcname} "
              f"args={spec.args}")

        if spec.funcname == "echo":
            output = "\n".join(spec.args)
            client.close(process.processid, [output], EXECUTOR_PRV)
            print(f"  → completed with output: {output}")
        else:
            client.fail(process.processid, [f"unknown function {spec.funcname}"],
                        EXECUTOR_PRV)
            print(f"  → failed (unknown function)")
    except Exception as e:
        print(f"  ! error: {e}")
        time.sleep(2)
```

Install the Python SDK:

```bash
pip install pycolonies
```

### Step 4 — Run the executor

In a fresh terminal:

```bash
export COLONIES_SERVER_HOST=localhost
export COLONIES_SERVER_PORT=50080
export COLONIES_SERVER_TLS=false
export COLONIES_COLONY_NAME=d7065e
export EXECUTOR_ID=<your-executor-id>
export EXECUTOR_PRVKEY=<your-executor-private-key>

python simple_executor.py
```

You should see:

```
Executor 5e0a8c2b7f... starting
Got process 8a3f1c... funcname=echo args=['hello from d7065e']
  → completed with output: hello from d7065e
```

In the dashboard, the process flips from `waiting` to `running` to `successful`. The output field shows your message.

You have just used ColonyOS end to end.

---

## Part 6 — Connect ColonyOS to BuildSim (Without MCP Yet)

Now make the executor do something real: highlight a room in BuildSim. Keep it simple — direct REST, no MCP.

### Step 1 — Extend the executor

Save this as `buildsim_executor.py`:

```python
"""
ColonyOS executor that knows how to call BuildSim's REST API directly.
Handles two function names: 'highlight_room' and 'set_actuator'.
"""
import os
import time
import requests

from pycolonies import colonies_client

EXECUTOR_PRV = os.environ["EXECUTOR_PRVKEY"]
COLONY_NAME  = os.environ["COLONIES_COLONY_NAME"]
BUILDSIM     = os.environ.get("BUILDSIM_URL", "http://localhost:9090")

client = colonies_client()

def get_active_session():
    """Find the most recently active browser session."""
    r = requests.get(f"{BUILDSIM}/api/sessions", timeout=2).json()
    r.sort(key=lambda s: s.get("last_ws_active", ""), reverse=True)
    return r[0]["id"] if r else None

def highlight_room(args):
    """args = [room_id_int, color_hex, opacity_float]"""
    if len(args) != 3:
        return None, "highlight_room expects 3 args: room_id, color, opacity"
    room_id, color, opacity = int(args[0]), args[1], float(args[2])
    sid = get_active_session()
    if sid is None:
        return None, "no active BuildSim session — open the viewer first"
    r = requests.put(
        f"{BUILDSIM}/api/sessions/{sid}/highlights",
        json=[{"room_id": room_id, "color": color, "opacity": opacity}],
        timeout=2)
    if r.status_code != 200:
        return None, f"BuildSim HTTP {r.status_code}: {r.text[:120]}"
    return f"highlighted room {room_id} {color} on session {sid[:8]}", None

def set_actuator(args):
    """args = [actuator_id, state]"""
    if len(args) != 2:
        return None, "set_actuator expects 2 args: actuator_id, state"
    actuator_id, state = args[0], args[1]
    r = requests.put(
        f"{BUILDSIM}/api/actuators/{actuator_id}/state",
        json={"state": state}, timeout=2)
    if r.status_code != 200:
        return None, f"BuildSim HTTP {r.status_code}: {r.text[:120]}"
    return f"set {actuator_id} = {state}", None

FUNCS = {
    "highlight_room": highlight_room,
    "set_actuator":   set_actuator,
}

print(f"BuildSim executor starting, BUILDSIM={BUILDSIM}")

while True:
    try:
        process = client.assign(COLONY_NAME, 5, EXECUTOR_PRV)
        if process is None:
            continue
        spec = process.spec
        print(f"got process {process.processid[:12]} funcname={spec.funcname}")

        fn = FUNCS.get(spec.funcname)
        if fn is None:
            client.fail(process.processid,
                        [f"unknown function {spec.funcname}"], EXECUTOR_PRV)
            continue

        output, error = fn(spec.args)
        if error:
            client.fail(process.processid, [error], EXECUTOR_PRV)
            print(f"  ✗ {error}")
        else:
            client.close(process.processid, [output], EXECUTOR_PRV)
            print(f"  ✓ {output}")
    except Exception as e:
        print(f"  ! error: {e}")
        time.sleep(2)
```

### Step 2 — Run it

```bash
# in the same terminal as before (env vars still set)
python buildsim_executor.py
```

### Step 3 — Submit a real function spec

Save this as `highlight.json`:

```json
{
  "conditions": {
    "colonyname": "d7065e",
    "executortype": "generic"
  },
  "funcname": "highlight_room",
  "args": ["5", "#ff0000", "0.6"],
  "maxwaittime": 60,
  "maxexectime": 10,
  "maxretries": 0
}
```

Submit:

```bash
colonies function submit --spec highlight.json
```

Watch the dashboard. The process appears, runs, completes. Switch to your BuildSim browser tab — one room is now highlighted red.

You just orchestrated a real building-control action through ColonyOS.

### Step 4 — Try the actuator

```json
{
  "conditions": {
    "colonyname": "d7065e",
    "executortype": "generic"
  },
  "funcname": "set_actuator",
  "args": ["level4-heater-A109-state", "on"],
  "maxwaittime": 60,
  "maxexectime": 10,
  "maxretries": 0
}
```

Submit it; the heater turns on. The room temperature in BuildSim starts climbing (provided `level4_sim_loop.py` is still running).

---

## Part 7 — What You've Learned at This Point

After running through parts 1–6, you understand:

- A **colony** is a tenant boundary.
- An **executor** is a worker that registers with a colony, declares capabilities, and polls the broker for work.
- A **function spec** is a JSON description of a unit of work.
- A **process** is a running instance of a function spec.
- The broker matches function specs to executors and tracks lifecycle.
- The dashboard shows everything in real time.
- A **Python executor** is roughly 30 lines of code: claim, dispatch on funcname, run, report.
- Function specs are how anything — a cron job, a developer, another agent — submits work to the colony.

This is the entire mental model. Adding more executors, more colonies, more function specs is just adding more of the same pieces.

---

## Part 8 — Where to Go Next

Several productive directions from here. Pick the one that matches what you want to learn next.

### Direction A — Add a cron schedule

ColonyOS supports cron-style scheduled submission of function specs. Submit one `highlight_room` every 5 seconds and watch the dashboard fill with rows.

```bash
colonies cron add --name d7065e \
                  --schedule "*/5 * * * * *" \
                  --spec highlight.json
```

### Direction B — Add a GPU executor (or a "training" executor)

Register a second executor — perhaps on the lab GPU server — with a different `type`, like `gpu` or `training`. Write a function that takes 5 minutes to "train" (just sleeps for now). Submit it with `executortype: "gpu"` in the conditions. Watch the broker route it to the right executor.

### Direction C — Replace direct REST with MCP

This is the Pattern C in the earlier discussion. The executor uses the MCP Python SDK as a client and talks to `buildingsim/mcp/server.py` instead of direct REST.

Outline:

1. Install MCP: `pip install mcp httpx`.
2. Start the BuildSim MCP server: `python buildingsim/mcp/server.py --buildsim-url http://localhost:9090`.
3. In the executor, instead of `requests.put(BUILDSIM + ...)`, use the MCP client to call `set_actuator_state` or `highlight_rooms`.
4. The function spec stays the same. Only the executor's internals change.

The win: any future agent — whether written in Python, Go, or running on a different ColonyOS executor — can use the same MCP tools. The MCP server becomes the canonical API to BuildSim, and ColonyOS becomes one of many possible consumers.

### Direction D — Submit an agent cycle as a function spec

Wrap your existing `level7_react_agent.py`'s `cycle()` function as a ColonyOS-callable function. Submit one per cycle from a cron. Now every agent decision is a process row in the ColonyOS dashboard, with logs and an audit trail.

---

## Part 9 — Things That Will Trip You Up

A few practical notes that save hours.

**Port conflicts.** ColonyOS's PostgreSQL uses port 5432 by default — the same port your TimescaleDB uses. Either change ColonyOS's port mapping in its compose file, or stop your other compose stack while running ColonyOS for the first time.

**Permission tiers.** ColonyOS has multiple roles: colony admin, executor, end user. Each has its own private key. Mixing up which key is set in `COLONIES_PRVKEY` causes "permission denied" errors that look mysterious until you remember the keys.

**Pull-based executors.** Executors don't appear "online" in the dashboard until they actually start polling. Registering an executor in the CLI is *not* the same as starting one. If a function spec stays `waiting` forever, check that an executor process is actually running.

**Conditions matter.** A function spec with `executortype: "gpu"` will never be assigned to an executor whose type is `generic`. Match these carefully.

**Output is just text.** When you call `client.close(process_id, [output_string], prv_key)`, the output is stored as a string. Structured output (JSON, files) needs to be marshalled to and from strings yourself.

**Cron syntax is six fields, not five.** ColonyOS uses second-precision cron. `*/5 * * * * *` is "every 5 seconds" with the extra leading field. Don't paste five-field cron from elsewhere.

---

## Part 10 — Reading the Dashboard

The dashboard at `http://localhost:3000` is the single best teacher for understanding what is happening. Key views:

- **Colonies** — the top-level list. Click yours.
- **Executors** — every registered executor with status. Green = connected and polling.
- **Processes** — every function spec ever submitted, with status (`waiting`, `running`, `successful`, `failed`). Click one to see input, output, logs, and timing.
- **Function specifications** — registered specs and history.
- **Crons** — scheduled jobs and their last-run status.

Submitting a function spec, then watching the process row move through `waiting → running → successful` (or `failed`), is the single quickest way to internalise the model.

---

## Part 11 — The Five-Sentence Summary

1. ColonyOS is a workload scheduler — it takes JSON descriptions of work and dispatches them to executors that have matching capabilities.
2. Four concepts make up the world: colony, executor, function spec, process; learn these four and the documentation makes sense.
3. The minimum useful executor is roughly thirty lines of Python: claim work, dispatch on the function name, run the function, report the result.
4. Once an executor knows how to call BuildSim's REST API, ColonyOS becomes a real workload orchestrator for building control — submit "highlight room", "set actuator", "run agent cycle" as function specs and watch them flow through the dashboard.
5. MCP comes next — replace the executor's direct REST calls with an MCP client, and your colony becomes one consumer among many of a building API that is the same for every agent on the planet.

When this all clicks, the path forward is just: more executors, more function specs, more cron schedules. The shape stays the same.
