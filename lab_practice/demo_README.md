# BuildSim × ColonyOS Chat Demo

A small, runnable demo that mimics the colonyos.io showcase:
a chat box on the right, the BuildSim 3D viewer on the left, and a ColonyOS
executor in the middle that turns natural-language prompts into actions on
the building.

## Architecture

```
   ┌──────────────────────────────┐
   │  Browser: demo_frontend.html │
   │   ┌────────────┐ ┌─────────┐ │
   │   │ BuildSim   │ │  Chat   │ │
   │   │ 3D viewer  │ │  box    │ │
   │   │ (iframe)   │ │         │ │
   │   └────────────┘ └────┬────┘ │
   └────────────────────────┼─────┘
                            │ POST /chat
                            ▼
   ┌──────────────────────────────┐
   │  demo_backend.py (Flask)     │
   │  POST /chat                  │
   │  └─ submits function spec    │
   │     to ColonyOS, waits       │
   └─────────────┬────────────────┘
                 │ function spec
                 ▼
   ┌──────────────────────────────┐
   │  ColonyOS broker             │
   └─────────────┬────────────────┘
                 │ dispatch
                 ▼
   ┌──────────────────────────────┐
   │  demo_executor.py            │
   │  reads the prompt, calls the │
   │  LLM (Ollama) with tool      │
   │  definitions, executes the   │
   │  tools against BuildSim's    │
   │  REST API                    │
   └─────────────┬────────────────┘
                 │ REST
                 ▼
   ┌──────────────────────────────┐
   │  BuildSim                    │
   │  state changes → WebSocket   │
   │  pushes to the iframe        │
   └──────────────────────────────┘
```

## Files

| File | Role |
|---|---|
| `demo_frontend.html` | The page that opens in the browser — embeds BuildSim and a chat box |
| `demo_backend.py` | Flask backend that accepts prompts and submits them to ColonyOS |
| `demo_executor.py` | ColonyOS executor that runs an LLM-driven loop against BuildSim |
| `demo_requirements.txt` | Python deps for the demo (Flask, pycolonies) |
| `demo_README.md` | This file |

## Prerequisites

Before running the demo, these must be alive on your laptop:

1. **BuildSim** on port 9090 (`make run` in `../buildingsim/`).
2. **Ollama** with a tool-calling model pulled (e.g. `ollama pull llama3.2:3b`).
3. **ColonyOS** broker running and a colony+executor pair created
   (follow `colonyos_starter.md` parts 3–4 first).

Environment variables the backend and executor expect:

```bash
export COLONIES_SERVER_HOST=localhost
export COLONIES_SERVER_PORT=50080
export COLONIES_SERVER_TLS=false
export COLONIES_COLONY_NAME=d7065e
export COLONIES_PRVKEY=<colony admin private key>      # for the backend
export EXECUTOR_PRVKEY=<executor private key>          # for the executor
export BUILDSIM_URL=http://localhost:9090
```

## Setup

```bash
cd /mnt/c/Users/dhikom/embedded/D7065E/lab_practice
source .venv/bin/activate                # if you use a venv
pip install -r demo_requirements.txt
```

## Running

Three terminals, in this order.

### Terminal 1 — the ColonyOS executor

```bash
python demo_executor.py
```

You should see `Demo executor started, waiting for agent_cycle work...` and
the executor flips to `running` in the ColonyOS dashboard.

### Terminal 2 — the Flask backend

```bash
python demo_backend.py
```

It binds to port 5000 by default. Look for `Running on http://localhost:5000`.

### Terminal 3 — open the frontend

The frontend is a static file. Open it directly in your browser:

```
file:///mnt/c/Users/dhikom/embedded/D7065E/lab_practice/demo_frontend.html
```

Or serve it with Python's built-in server if you prefer:

```bash
python -m http.server 8080
# then open http://localhost:8080/demo_frontend.html
```

## Try it

Type prompts in the chat box. The executor understands a small set of intents:

- **Highlight a room** — e.g. `highlight room A109 in red`, `mark A2306 yellow`
- **Set an actuator** — e.g. `turn the heater on in A109`, `turn off level4-heater-A109-state`
- **Read a sensor** — e.g. `what is the temperature in A109?`, `read smoke in A2306`
- **Find a route** — e.g. `find a route from A109 to A2306`

Each prompt becomes a ColonyOS function spec, the executor picks it up,
the LLM reasons about it, the tools run against BuildSim, and the 3D
viewer in the iframe updates via BuildSim's WebSocket push.

## What you should see while it runs

- **ColonyOS dashboard** (`http://localhost:3000`): a new `agent_cycle`
  process appears in `waiting`, then `running`, then `successful` for
  each prompt.
- **BuildSim 3D viewer** (in the iframe): the room changes colour,
  the actuator state updates, the route lights up.
- **Chat panel**: shows the prompt and the agent's natural-language
  reply.

## Troubleshooting

**"connection refused" from the backend** — ColonyOS isn't running or
the COLONIES_* env vars are wrong.

**Frontend can't reach backend (CORS)** — the backend uses `flask-cors`
to allow `file://` and `http://localhost:*` origins. If you serve the
frontend through a different scheme, add it to `CORS_ALLOWED_ORIGINS`
in `demo_backend.py`.

**Iframe shows "site can't be reached"** — BuildSim isn't running on
9090. Check `curl http://localhost:9090/api/building` in a terminal.

**Process stays in `waiting` forever** — the executor isn't polling.
Check terminal 1 for errors. Verify `EXECUTOR_PRVKEY` matches a real
registered executor.

**Ollama timeouts** — the LLM call has a 15-second hard timeout. If
your model is large or your GPU is busy, increase `LLM_TIMEOUT` in
`demo_executor.py`. Or pull a smaller model: `ollama pull llama3.2:3b`.

## Extending the demo

The cleanest extensions, in order of effort:

1. **Add more intents** — extend the executor's tool catalogue with
   `set_occupancy` (place people on the map), `set_coverage` (draw
   coverage spheres), or anything else from BuildSim's session API.
2. **Stream the LLM response** — replace the synchronous `client.wait`
   call with a polling loop and stream tokens as they arrive.
3. **Add memory** — let the agent see the conversation history. Store
   it in the backend keyed by browser-session-id.
4. **Swap REST for MCP** — make the executor speak to BuildSim through
   `buildingsim/mcp/server.py` instead of direct REST. The Pattern C
   step from the architecture discussion.
