# BuildSim × ColonyOS × Chat Demo — Full Guide

The complete document for setting up and running the autonomous building-control demo: a chat box on the right of the screen, a 3D building viewer on the left, and behind the scenes an LLM-driven agent dispatched by ColonyOS to a vLLM server in the lab. Type a request in plain English, watch the building respond.

Everything you need to set this up from scratch is in this document.

---

## Part 1 — What You're Building

A single web page with two halves. On the left, the BuildSim 3D viewer showing rooms, walls, and equipment. On the right, a chat box. You type "highlight room A109 in red," press Enter, and within a few seconds the room turns red.

Behind that single visible action: a Flask backend receives the prompt, packages it as a ColonyOS function spec, the ColonyOS broker schedules it to an executor, the executor calls an LLM hosted on the lab's RTX 5090 GPU, the LLM emits a structured tool call, the executor translates the tool call into a REST request against BuildSim, BuildSim updates its in-memory state and pushes the change down a WebSocket to the browser's iframe.

Five processes collaborate to produce one room turning red. The demo proves the three-layer architecture the course teaches: orchestrator (ColonyOS), agent (LLM + tools), and substrate (BuildSim) — each independently swappable.

```
   ┌──────────────────────────────────────────────────┐
   │  Browser                                         │
   │  ┌────────────────────────┐  ┌────────────────┐  │
   │  │ BuildSim 3D viewer     │  │ Chat box       │  │
   │  │ (iframe to :9090)      │  │                │  │
   │  └────────────▲───────────┘  └────────┬───────┘  │
   └───────────────│───────────────────────│──────────┘
                   │ WebSocket             │ POST /chat
                   │ (push updates)        │
                   │                       ▼
   ┌───────────────│────────────────────────────────────┐
   │  BuildSim     │           demo_backend.py (Flask)  │
   │  :9090        │                                    │
   │  REST + WS    │           submits function spec    │
   └───────────────┘─────┐                              │
                         │     ┌────────────────────────┘
                         │     │
                         │     ▼
                         │  ┌────────────────────┐
                         │  │  ColonyOS broker   │
                         │  │  :50080            │
                         │  └─────────┬──────────┘
                         │            │ dispatch
                         │            ▼
                         │  ┌────────────────────┐
                         └─◄│  demo_executor.py  │──►  vLLM
                            │  (Python)          │     carbon.eislab.se:8000
                            └────────────────────┘     google/gemma-4-E4B-it
```

---

## Part 2 — The Architecture in Plain Language

Three layers, each with a clear responsibility.

### Layer 1 — Orchestrator (ColonyOS)

The scheduler. Decides *when* and *where* work runs. Takes a JSON description of work (called a function spec), finds a worker (executor) that can do it, dispatches the work, tracks the lifecycle, returns the result.

A useful image: a courier service. Picks up an envelope, looks at the address, delivers it to the right office. Doesn't read the envelope's contents.

### Layer 2 — Agent (the executor + LLM + tools)

The brain. Receives a prompt, reasons about it, decides which tool to call, executes the tool against the building. Powered by an LLM (Gemma 4 on the lab's GPU) plus a small catalogue of BuildSim-related tools (highlight a room, set an actuator, read a sensor, find a route).

A useful image: a junior building manager who reads each request and decides which lever to pull. They don't generate the requests themselves; they react to ones forwarded to them.

### Layer 3 — Substrate (BuildSim)

The building itself, in software form. Holds the current state of every sensor and actuator, renders the 3D viewer, exposes a REST API for everyone else to use. Has no knowledge of ColonyOS or the agent — anyone who can speak HTTP can use it.

A useful image: the building itself. The thermostat doesn't know who is in the room; it just provides a measurement and accepts commands.

### The interfaces between layers

| From → To | Protocol | Purpose |
|---|---|---|
| Browser → Backend | HTTP POST `/chat` | Send the prompt |
| Backend → Broker | ColonyOS API (HTTPS or HTTP) | Submit a function spec |
| Broker → Executor | ColonyOS pull-based assign | Dispatch the work |
| Executor → vLLM | HTTP POST `/v1/chat/completions` | Reasoning |
| Executor → BuildSim | HTTP REST | Tool calls (highlight, set actuator, etc.) |
| BuildSim → Browser | WebSocket push | UI updates |

Three independent layers. Three swappable pieces. Swap the LLM provider — no other layer notices. Swap BuildSim for a real building — no other layer notices. Swap ColonyOS for Kubernetes — no other layer notices. That's the point of the architecture.

---

## Part 3 — ColonyOS Concepts (Quick Reference)

Four ideas make up ColonyOS. Internalise these and the rest follows.

### Colony

A tenant boundary. Everything inside one colony can talk; two colonies don't see each other. In this demo there's one colony, named `d7065e`.

### Executor

A worker process that registers with a colony, declares its capabilities (a string tag like `demo-agent`), and pulls work from the broker.

### Function specification

A JSON document describing one unit of work. Names the function (`funcname`), passes arguments (`args`), specifies which kind of executor can handle it (`conditions.executortype`), sets deadlines (`maxexectime`, `maxretries`).

### Process

A running instance of a function spec. The broker creates one each time a spec is dispatched. Lifecycle: `waiting` → `running` → `successful` or `failed`.

### The keys

ColonyOS uses Ed25519 cryptographic identity instead of passwords. Three keypairs exist in this demo (only two are actively used).

| Keypair | Owned by | Used by | What for |
|---|---|---|---|
| Colony keypair | The colony itself | `demo_backend.py` and CLI admin commands | Submitting work, registering executors |
| Executor keypair | The executor process | `demo_executor.py` | Polling the broker, closing processes |
| User keypair | A non-admin submitter | (not used in this demo) | Production-grade submission without admin rights |

When something errors with "permission denied," it's almost always because the wrong private key is in the wrong env var. The colony's private key goes in `COLONIES_PRVKEY` (for the backend). The executor's private key goes in `EXECUTOR_PRVKEY` (for the executor).

---

## Part 4 — Prerequisites

Before touching any of the demo files, the following must be in place.

### Software on the laptop

```bash
docker ps                                # Docker daemon running
python3 --version                        # 3.10+
go version                               # 1.22+ (for the colonies CLI)
which curl
```

### Network access

Two outbound destinations need to work:

- The lab vLLM at `http://carbon.eislab.se:8000`. Test:
  ```bash
  curl -s http://carbon.eislab.se:8000/v1/models | head -c 200
  ```
  If this times out or "connection refused," connect to the LTU VPN first.

- GitHub (to clone ColonyOS):
  ```bash
  curl -s https://github.com >/dev/null && echo "GitHub reachable"
  ```

### Repository layout

This guide assumes everything is under `C:\Users\dhikom\D7065E\` (`/mnt/c/Users/dhikom/D7065E/` from WSL):

```
D7065E/
├── buildingsim/             ← BuildSim source + binary
├── lab_practice/            ← demo files (this folder)
│   ├── demo_backend.py
│   ├── demo_executor.py
│   ├── demo_frontend.html
│   ├── demo_requirements.txt
│   ├── DEMO_FULL_GUIDE.md   ← this document
│   └── (other helper files)
└── ...
```

---

## Part 5 — One-Time Setup

You do this once. Everything from Part 6 onward is per-demo, takes 90 seconds.

### Step 5.1 — Free port 5432 (if TimescaleDB is using it)

ColonyOS's PostgreSQL wants port 5432. So does your TimescaleDB from level 4. Stop it before installing ColonyOS:

```bash
cd /mnt/c/Users/dhikom/D7065E/lab_practice
docker compose stop timescaledb 2>/dev/null || true
docker ps                                  # confirm timescaledb is gone
```

You can restart TimescaleDB later when you're not running the demo.

### Step 5.2 — Install ColonyOS

Clone, start the broker, install the CLI.

```bash
# Clone the repo
cd ~
git clone https://github.com/colonyos/colonies.git
cd colonies

# Find the env file the compose setup expects
ls ~/colonies/docker/docker-compose.env

# Bring up the broker (uses docker-compose.env for variables)
cd ~/colonies/docker
docker compose --env-file docker-compose.env up -d
docker compose --env-file docker-compose.env ps
```

You should see the `colonies-server` and `colonies-postgres` containers running. The web dashboard is not bundled in this compose file — you'll work entirely from the CLI, which is enough for the demo.

Install the CLI:

```bash
cd ~/colonies
go install ./cmd/colonies
echo 'export PATH=$PATH:~/go/bin' >> ~/.bashrc
source ~/.bashrc
colonies version                            # should print a version
```

### Step 5.3 — Set the ColonyOS env vars persistently

Add these to `~/.bashrc` so every new terminal has them automatically:

```bash
cat >> ~/.bashrc <<'EOF'
export COLONIES_SERVER_HOST=localhost
export COLONIES_SERVER_PORT=50080
export COLONIES_SERVER_TLS=false
export COLONIES_COLONY_NAME=d7065e
EOF
source ~/.bashrc

colonies server status                      # should print server info
```

### Step 5.4 — Create the colony and the executor

This is the longest step but you only do it once. Save the four hex strings it produces into a notes file.

```bash
# Generate the colony's keypair
colonies key generate
# Output:
#   ID:     <COLONY_ID>            ← save this
#   PrvKey: <COLONY_PRIVATE_KEY>   ← save this (secret)
```

Register the colony:

```bash
colonies colony add --colonyid <COLONY_ID> --name d7065e
```

Generate the executor's keypair:

```bash
colonies key generate
# Output:
#   ID:     <EXECUTOR_ID>            ← save this
#   PrvKey: <EXECUTOR_PRIVATE_KEY>   ← save this (secret)
```

Register the executor with type `demo-agent` (the backend submits specs targeting this exact type):

```bash
export COLONIES_PRVKEY=<COLONY_PRIVATE_KEY>
colonies executor add \
  --executorid <EXECUTOR_ID> \
  --name laptop-demo-agent \
  --type demo-agent \
  --colonyname d7065e
```

Confirm:

```bash
colonies executor ls --colonyname d7065e
```

You should see one executor with type `demo-agent`. It will show as `pending` until you actually start the executor process in Part 6.

### Step 5.5 — Write down what you need

Open a notes file (NOT in git, NOT in the repo — somewhere private). Save these:

```
DEMO_NOTES.txt (private)
─────────────────────────
COLONY_ID:             <hex>
COLONY_PRIVATE_KEY:    <hex>     # NEVER commit
EXECUTOR_ID:           <hex>
EXECUTOR_PRIVATE_KEY:  <hex>     # NEVER commit
```

### Step 5.6 — Set up a Python venv with the demo dependencies

```bash
cd /mnt/c/Users/dhikom/D7065E/lab_practice
python3 -m venv .venv
source .venv/bin/activate
pip install -r demo_requirements.txt

# Verify
python -c "import flask, pycolonies, requests; print('ok')"
```

If `pycolonies` fails to install, your system Python may not have the right version. Try `pip install --upgrade pip` then retry.

### Step 5.7 — Verify vLLM is reachable

```bash
curl -s http://carbon.eislab.se:8000/v1/models | head -c 300
```

You should get JSON listing the models. If you don't, connect to the LTU VPN.

One-time setup is now done. Everything from here is per-demo.

---

## Part 6 — Per-Demo Setup (Every Time You Demo)

Six terminals. About 90 seconds total once you've done it once.

### Terminal 1 — BuildSim

```bash
cd /mnt/c/Users/dhikom/D7065E/buildingsim
make run
```

Wait for `Server starting on :9090`. Leave open.

### Terminal 2 — Browser tab on BuildSim

In Chrome (not Safari — Safari blocks WebSockets over plain HTTP):

```
http://localhost:9090
```

The 3D building loads. **Keep this tab open.** The executor uses the most-recently-active session to push highlights to.

### Terminal 3 — Confirm ColonyOS is running

```bash
docker ps                                   # should show colonies-server + colonies-postgres
curl -s http://localhost:50080/api/version 2>&1 | head -c 100
```

If neither container is running, restart them:

```bash
cd ~/colonies/docker
docker compose --env-file docker-compose.env up -d
```

### Terminal 4 — The demo executor

```bash
cd /mnt/c/Users/dhikom/D7065E/lab_practice
source .venv/bin/activate

# ColonyOS vars (from ~/.bashrc — should already be set)
# Confirm with: env | grep COLONIES

export EXECUTOR_PRVKEY=<EXECUTOR_PRIVATE_KEY from your notes>
export BUILDSIM_URL=http://localhost:9090

# LLM vars are now the defaults but you can override
# export LLM_BACKEND=openai
# export LLM_URL=http://carbon.eislab.se:8000/v1/chat/completions
# export LLM_MODEL=google/gemma-4-E4B-it

python demo_executor.py
```

You should see:

```
[executor] starting, colony=d7065e
[executor] LLM backend=openai, model=google/gemma-4-E4B-it, url=http://carbon.eislab.se:8000/v1/chat/completions
[executor] BuildSim=http://localhost:9090
[executor] waiting for agent_cycle work...
```

### Terminal 5 — The Flask backend

```bash
cd /mnt/c/Users/dhikom/D7065E/lab_practice
source .venv/bin/activate

# ColonyOS vars (from ~/.bashrc — should already be set)
export COLONIES_PRVKEY=<COLONY_PRIVATE_KEY from your notes>

python demo_backend.py
```

You should see:

```
[backend] starting on :5000  colony=d7065e
 * Running on http://0.0.0.0:5000
```

### Terminal 6 — Open the demo frontend

In Chrome, open the HTML file directly. Two ways:

**Direct file URL:**
```
file:///mnt/c/Users/dhikom/D7065E/lab_practice/demo_frontend.html
```

**Or serve it with a tiny HTTP server** (better for browser CORS):
```bash
cd /mnt/c/Users/dhikom/D7065E/lab_practice
python -m http.server 8080
```
then open `http://localhost:8080/demo_frontend.html`.

You should see: BuildSim 3D viewer on the left, chat panel on the right, greeting message, input box.

**All six pieces are now running.** Time to test.

---

## Part 7 — Running the Demo

In the chat box, type one of these. Press Enter.

| Prompt | What should happen |
|---|---|
| `highlight room A109 in red` | Room A109 turns red in the 3D viewer |
| `mark A2306 yellow` | Room A2306 turns yellow |
| `clear the highlights` | All highlights disappear |
| `turn on level4-heater-A109-state` | Heater actuator state flips to "on" |
| `what is the temperature in A109?` | The agent replies with the current value |
| `find a route from A109 to A2306` | A path is drawn on the 3D map |

### What to watch in three places simultaneously

For a satisfying demo, set up two side-by-side browser windows:

```
   ┌──────────────────────────────────┐ ┌──────────────────────────────────┐
   │  Window 1 (the demo)             │ │  Window 2 (the executor terminal)│
   │  ┌────────────┐ ┌─────────────┐  │ │                                  │
   │  │ BuildSim   │ │ Chat box    │  │ │  [executor] got process abc12... │
   │  │ 3D viewer  │ │             │  │ │  [executor] prompt: 'highlight…' │
   │  └────────────┘ └─────────────┘  │ │    [tool] highlight_room(...)... │
   │                                  │ │  [executor] → Highlighted room A1│
   └──────────────────────────────────┘ └──────────────────────────────────┘
```

You type in the chat. Watch the executor terminal print the tool call. Watch the room change colour. All within 2–4 seconds.

### To verify ColonyOS scheduled the work

In another terminal:

```bash
colonies process pss --colonyname d7065e --count 5     # last 5 successful processes
colonies process ps  --colonyname d7065e               # currently running
```

You should see one row per chat prompt, with status `successful` and the prompt visible in the spec.

---

## Part 8 — Tracing One Prompt End-to-End

For when you need to explain the architecture at the oral exam. One prompt, all five processes, frame by frame.

The user types `"highlight room A109 in red"` and presses Enter.

1. **Browser** — the JS in `demo_frontend.html` POSTs `{"prompt":"highlight room A109 in red"}` to `http://localhost:5000/chat`.

2. **`demo_backend.py`** receives the POST. It builds a function spec:
   ```json
   {
     "conditions": {"colonyname":"d7065e","executortype":"demo-agent"},
     "funcname":   "agent_cycle",
     "args":       ["highlight room A109 in red"],
     "maxwaittime":30, "maxexectime":60, "maxretries":0
   }
   ```
   Calls `client.submit(spec, COLONY_PRVKEY)`, gets back a process ID, starts polling for completion.

3. **ColonyOS broker** records the function spec, marks it `waiting`, waits for an executor with type `demo-agent` to claim it.

4. **`demo_executor.py`** is blocked in `client.assign(...)`. The broker hands it the new process. The executor sees `funcname=agent_cycle` and args = `["highlight room A109 in red"]`.

5. **Executor** calls `run_cycle("highlight room A109 in red")`. Builds a chat history:
   ```
   [
     {"role":"system","content":"You are a building-control agent. ..."},
     {"role":"user",  "content":"highlight room A109 in red"}
   ]
   ```

6. **Executor** POSTs to `http://carbon.eislab.se:8000/v1/chat/completions` with the messages and the tool schemas. The vLLM-hosted Gemma 4 sees the prompt, picks `highlight_room`, returns:
   ```json
   {"choices":[{"message":{
     "content": "",
     "tool_calls":[{"function":{
       "name":"highlight_room",
       "arguments":{"room":"A109","color":"#ff0000"}
     }}]
   }}]}
   ```

7. **Executor** parses the tool call. Looks up `tool_highlight_room` in its dispatch table. Calls it with `{"room":"A109","color":"#ff0000"}`.

8. **`tool_highlight_room`** does three things:
   - Calls `find_room_id("A109")` → searches each floor's room list → returns the integer ID (e.g. 42).
   - Calls `get_active_session()` → returns the browser's session UUID.
   - PUTs to BuildSim: `PUT /api/sessions/<sid>/highlights` with `[{"room_id":42,"color":"#ff0000","opacity":0.6}]`.
   - Returns `{"ok":true,"room":"A109","room_id":42,...}`.

9. **BuildSim** stores the highlight in the session state. Pushes a `highlights` message on the WebSocket to every connected browser. The 3D iframe in `demo_frontend.html` re-renders — A109 is now red.

10. **Executor** appends the tool result to the conversation. Calls vLLM again with the augmented history. The model now sees that the tool succeeded and emits a final reply:
    ```json
    {"choices":[{"message":{
      "content":"Highlighted room A109 in red.",
      "tool_calls":[]
    }}]}
    ```

11. **Executor** sees no more tool calls. Done. Returns `"Highlighted room A109 in red."` from `run_cycle()`.

12. **Executor** calls `client.close(process_id, ["Highlighted room A109 in red."], EXECUTOR_PRV)`. The broker marks the process `successful`.

13. **`demo_backend.py`** is still polling. On its next `client.get_process()` it sees state `2` (success), reads the output, returns:
    ```json
    {"status":"success","output":"Highlighted room A109 in red.","process_id":"..."}
    ```

14. **Browser** receives the JSON. The JS appends a new chat bubble: "Highlighted room A109 in red."

The user sees: A109 turning red (step 9) at roughly the same moment as the chat reply (step 14). The total elapsed time is typically 1.5 to 4 seconds, dominated by the vLLM call.

---

## Part 9 — Troubleshooting

The common failure modes, in roughly the order they appear when something is new.

### Setup errors

**`docker compose up` fails with "invalid proto: ..."** — the env file isn't being loaded. Use `--env-file docker-compose.env`. See Part 5.2.

**`docker compose up` complains port 5432 is in use** — TimescaleDB is still running. Stop it: `docker compose -f /mnt/c/Users/dhikom/D7065E/lab_practice/docker-compose.yml stop timescaledb`.

**`colonies` command not found** — the Go install location isn't on PATH. `export PATH=$PATH:~/go/bin` and add it to `~/.bashrc`.

**`colonies key generate` errors with "connection refused"** — broker not running. `cd ~/colonies/docker && docker compose --env-file docker-compose.env up -d`.

### Demo runtime errors

**Backend won't start with "COLONIES_PRVKEY is not set"** — you forgot the export. Export the colony private key.

**Process stays `waiting` forever in `colonies process psw`** — the executor isn't running, OR its type doesn't match. Check Terminal 4. Confirm the executor was registered with `--type demo-agent` (case-sensitive).

**Executor crashes with "permission denied"** — wrong `EXECUTOR_PRVKEY`. It's the executor's private key, not the colony's.

**Executor connects to ColonyOS but says "LLM HTTP 404"** — your `LLM_URL` is missing `/v1/chat/completions` at the end. Should be `http://carbon.eislab.se:8000/v1/chat/completions` (full path, not just the base).

**Executor says "LLM HTTP 401" or "403"** — vLLM is asking for auth. Set `LLM_API_KEY=<actual-token>` rather than the default `not-needed`.

**Executor says "Connection refused" to vLLM** — you're not on the network that can reach `carbon.eislab.se`. Connect to LTU VPN.

**Chat says "Highlighted room A109 in red" but the room doesn't actually turn red** — the LLM is describing the action in text instead of emitting a real tool call. Means vLLM's tool-call parser isn't enabled for this model. Two workarounds:
  - Ask the lab admin to restart vLLM with `--enable-auto-tool-choice --tool-call-parser gemma`.
  - Switch to local Ollama: `export LLM_BACKEND=ollama` then start with a tool-calling model like `llama3.2:3b`.

**"no active BuildSim session — open the viewer first"** — the executor needs at least one browser tab connected to BuildSim. Confirm Terminal 2 (the BuildSim tab) is still open and showing the building.

**Chat works once, then nothing happens on the second prompt** — backend's blocking poll is starving subsequent requests. Open another browser tab and try a fresh prompt there; or restart `demo_backend.py`. The single-threaded Flask dev server is the bottleneck.

**Room name not found ("unknown room 'a 109'")** — typos and weird spacing. The lookup is case-insensitive but exact otherwise. Use the room names exactly as listed: `curl http://localhost:9090/api/building/floors/level0 | head -c 400`.

**CORS error in browser console** — open the HTML through `python -m http.server 8080` instead of as a `file://`. Some browsers block `file://` from making `fetch` calls.

### Recovery

When something gets into a confused state, the nuclear option is:

```bash
# Stop everything
docker compose --env-file ~/colonies/docker/docker-compose.env -f ~/colonies/docker/docker-compose.yml down
# Restart ColonyOS
cd ~/colonies/docker && docker compose --env-file docker-compose.env up -d
# Restart BuildSim (Ctrl-C in Terminal 1, then `make run`)
# Restart executor (Ctrl-C in Terminal 4, then re-run)
# Restart backend (Ctrl-C in Terminal 5, then re-run)
# Refresh the browser tab on the demo
```

About 30 seconds of cold-restart time.

---

## Part 10 — Extending the Demo

Once the basics work, here are the natural next steps in order of effort.

### Add more intents

Edit `demo_executor.py`:

1. Write a new `tool_*` function following the same pattern.
2. Add it to the `TOOLS` dict.
3. Add a schema to `TOOL_SCHEMAS`.

Examples worth adding:
- `tool_place_person` — call BuildSim's `/api/sessions/{sid}/occupancy` to put a person icon in a room.
- `tool_set_coverage` — draw a translucent sphere via `/api/sessions/{sid}/coverage`.
- `tool_list_equipment` — query `/api/equipment` and return a summary.

The LLM discovers new tools automatically; no other changes needed.

### Replace direct REST with MCP

Currently each `tool_*` function makes a direct REST call to BuildSim. The course's `buildingsim/mcp/server.py` exposes the same operations as MCP tools.

Refactor: replace each `requests.put(...)` with a call through an MCP client. The function spec and the LLM-side flow don't change. The win is that any other MCP-capable agent — Claude Code, Cursor, a different team's agent — can use the same tools without writing their own integration.

### Add an audit trail to TimescaleDB

The `db.py` schema from level 4 already has a `decisions` hypertable. Make the executor write a row for each tool call. After a demo, you can replay every decision the agent made via SQL queries — solid Grade-4 audit-trail material.

### Schedule prompts with ColonyOS cron

Submit one `agent_cycle` every minute that asks "report the current state of room A109." Each becomes a process in the dashboard. A great demonstration of ColonyOS as a long-running orchestrator.

### Multi-agent decomposition

Run two executors, one of type `safety-agent` and one of type `comfort-agent`. The backend submits to whichever type fits the prompt. A coordinator process (a third executor) resolves conflicts. The full Lecture 4 §Multi-Agent setup, demoed live.

---

## Part 11 — Quick Reference Card

For when you've got the demo working but need the commands fast.

### Cold-start everything (after a reboot)

```bash
# 1. ColonyOS
cd ~/colonies/docker
docker compose --env-file docker-compose.env up -d

# 2. BuildSim (Terminal 1)
cd /mnt/c/Users/dhikom/D7065E/buildingsim && make run

# 3. Browser tab on BuildSim
# Open http://localhost:9090

# 4. Executor (Terminal 4)
cd /mnt/c/Users/dhikom/D7065E/lab_practice
source .venv/bin/activate
export EXECUTOR_PRVKEY=<exec key>
export BUILDSIM_URL=http://localhost:9090
python demo_executor.py

# 5. Backend (Terminal 5)
cd /mnt/c/Users/dhikom/D7065E/lab_practice
source .venv/bin/activate
export COLONIES_PRVKEY=<colony key>
python demo_backend.py

# 6. Frontend
# Open file:///mnt/c/Users/dhikom/D7065E/lab_practice/demo_frontend.html
```

### CLI commands for inspecting ColonyOS

```bash
colonies colony ls                                # list colonies
colonies executor ls --colonyname d7065e          # list executors
colonies process ps  --colonyname d7065e          # currently running
colonies process pss --colonyname d7065e          # successful
colonies process psf --colonyname d7065e          # failed
colonies process get -p <process-id>              # one process in detail
colonies process delete -p <process-id>           # remove
```

### Useful curls

```bash
# BuildSim health
curl -s http://localhost:9090/api/building | head -c 200

# ColonyOS health
curl -s http://localhost:50080/api/version

# vLLM health
curl -s http://carbon.eislab.se:8000/v1/models | head -c 300

# List rooms on level 0
curl -s http://localhost:9090/api/building/floors/level0 | head -c 400
```

### Env vars cheat sheet

| Var | Set in | Value |
|---|---|---|
| `COLONIES_SERVER_HOST` | ~/.bashrc | `localhost` |
| `COLONIES_SERVER_PORT` | ~/.bashrc | `50080` |
| `COLONIES_SERVER_TLS` | ~/.bashrc | `false` |
| `COLONIES_COLONY_NAME` | ~/.bashrc | `d7065e` |
| `COLONIES_PRVKEY` | Terminal 5 (backend) | colony private key |
| `EXECUTOR_PRVKEY` | Terminal 4 (executor) | executor private key |
| `BUILDSIM_URL` | Terminal 4 | `http://localhost:9090` |
| `LLM_BACKEND` | Terminal 4 (optional) | `openai` (default) or `ollama` |
| `LLM_URL` | Terminal 4 (optional) | `http://carbon.eislab.se:8000/v1/chat/completions` (default) |
| `LLM_MODEL` | Terminal 4 (optional) | `google/gemma-4-E4B-it` (default) |
| `LLM_API_KEY` | Terminal 4 (optional) | `not-needed` (default) |

---

## Part 12 — Summary in Five Sentences

1. The demo is a single web page that lets a user type natural-language commands and watch the BuildSim 3D building respond, with ColonyOS scheduling each prompt as a function spec and an executor running an LLM that translates prompts into tool calls.
2. The architecture has three independently swappable layers — orchestrator (ColonyOS), agent (executor + LLM + tools), and substrate (BuildSim) — connected only through their well-defined interfaces.
3. The one-time setup installs ColonyOS, generates two keypairs (colony + executor), registers them, and installs the Python dependencies; it takes about 30 minutes the first time.
4. Per-demo setup is six terminals (BuildSim, browser, ColonyOS verification, executor, backend, frontend) and takes about 90 seconds once you know the sequence.
5. When something doesn't work, almost every failure is one of: wrong private key in the wrong env var, vLLM unreachable (VPN), wrong executor type registered, or `--env-file docker-compose.env` forgotten.

Keep this guide alongside the demo files. Treat it as the handover document for your future self.
