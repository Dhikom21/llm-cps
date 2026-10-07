"""
llm_edge_agent.py — the control loop, with a language model deciding.

PERCEIVE the rooms, let the model REASON and ACT through tools, GUARD every
action it proposes, PUBLISH the result to the twin, and record the lot.

--------------------------------------------------------------------------
Why tool-calling rather than a single prompt
--------------------------------------------------------------------------
The model is not handed a tidy summary and asked for an answer. It is given
tools and decides what to look at: read_sensors for the current state,
query_history to write its own SQL over the readings, set_actuator to act,
create_alert to tell a human something. Several round trips per cycle, bounded
by MAX_HOPS.

That is a deliberate choice to study the harder case. An agent that chooses its
own evidence can reach a conclusion its evidence does not support — run a
query, get an error or zero rows, and report a finding anyway. A design that
only ever told the model what to think about could not produce that failure,
and it is the failure most likely to matter when such systems are deployed.

Every tool call and every result is written to the audit log, so a claim can be
checked against the evidence the agent actually had.

--------------------------------------------------------------------------
What the model cannot do
--------------------------------------------------------------------------
Reach a pin. set_actuator routes through pi_guard, which refuses a phantom room
or actuator (H1), an illegal state (H2) and a stale reading (H4), flags a
right-action-wrong-room command (H5), and drops no-ops. read_sensors and
query_history are perception: they can return nothing useful, but they cannot
do harm.

--------------------------------------------------------------------------
Configuration
--------------------------------------------------------------------------
    export LLM_BASE_URL=https://canopus.eislab.se/v1
    export LLM_MODEL=<exact id from GET /v1/models>
    export LLM_API_KEY=sk-...
    export CYCLE_S=10
    export MAX_HOPS=5

Any OpenAI-compatible server works, but the model must support native
tool-calling. If the server rejects the tools parameter the cycle fails and the
loop falls back to the state the specification requires, which is logged.

Run:
    pkill -f edge_agent.py              # GPIO pins cannot be shared
    export BUILDSIM_URL=http://localhost:9090
    python3 llm_edge_agent.py
"""
import json
import os
import re
import signal
import time

import requests

import agent_tools
import edge_agent_rules
import history
import pi_guard
import prompts
import rooms
import twin

# AGENT_IO decides where the agent gets the world from.
#
#   bus     (default) the split architecture: pi_sensor.py publishes readings,
#           pi_actuator.py owns the pins, and this process only reads the bus
#           and asks. The agent holds no hardware at all.
#
#   direct  the single-process arrangement: this process reads the sensors and
#           drives the pins itself. Simpler to run, and the shield sits in the
#           same process as the model rather than behind a boundary.
AGENT_IO = os.environ.get("AGENT_IO", "bus").lower()

if os.environ.get("HW", "real") == "fake":
    import fake_hardware as hw
else:
    import real_hardware as hw

CYCLE_S  = float(os.environ.get("CYCLE_S", "10.0"))
MAX_HOPS = int(os.environ.get("MAX_HOPS", "5"))

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL    = os.environ.get("LLM_MODEL", "qwen2.5:3b")
LLM_API_KEY  = os.environ.get("LLM_API_KEY", "ollama")
LLM_TIMEOUT  = float(os.environ.get("LLM_TIMEOUT", "60"))

# Prompts live in prompts.py so the evaluation harness tests the EXACT text the
# live agent uses. Two copies of a prompt drift apart within a day.
_ids = prompts.ACTUATOR_IDS
REACT_PROMPT = prompts.REACT_PROMPT


# ---------------- model transport ----------------
def _chat(messages, tools=None):
    body = {"model": LLM_MODEL, "temperature": 0, "messages": messages}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    r = requests.post(f"{LLM_BASE_URL}/chat/completions", json=body,
                      headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                      timeout=LLM_TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


# ---------------- the ReAct cycle ----------------
def run_react(building, snapshot):
    """Several round trips. The model chooses what to look at and when to act.

    The loop ends when the model replies without requesting a tool, or when
    MAX_HOPS is reached. The hop limit is a safety device, not an optimisation:
    without it a confused model can call read_sensors forever and the control
    loop simply stops running.
    """
    messages = [{"role": "system", "content": REACT_PROMPT},
                {"role": "user",
                 "content": f"Cycle at {time.strftime('%H:%M:%S')}. "
                            f"Decide what to do."}]

    for hop in range(MAX_HOPS):
        msg = _chat(messages, tools=agent_tools.SCHEMAS)
        thought = (msg.get("content") or "").strip()
        calls = msg.get("tool_calls") or []

        if thought:
            print(f"  [thought] {thought[:200]}")

        if not calls:
            pi_guard.audit({"source": "llm", "tool": "final", "text": thought,
                            "snapshot_read_at": snapshot.get("read_at")})
            return

        messages.append({"role": "assistant", "content": thought,
                         "tool_calls": calls})

        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name")
            raw = fn.get("arguments")
            if isinstance(raw, str):
                try:
                    args = json.loads(raw)
                except ValueError:
                    args = {}
            else:
                args = raw or {}

            print(f"  [action] {name}({json.dumps(args)[:140]})")
            result = agent_tools.dispatch(name, args, building, snapshot,
                                          source="llm")
            agent_tools.log_tool_call(name, args, result, snapshot)
            print(f"  [observ] {json.dumps(result, default=str)[:180]}")

            # Mirror a successful action into the twin so the floor plan shows
            # what the hardware is actually doing.
            #
            # The state comes from the TOOL NAME, not from the arguments —
            # which is the same reason the tools are split in the first place.
            # This previously tested for a tool called "set_actuator" that no
            # longer exists, so the condition was never true and the viewer
            # silently stopped reflecting anything the model did. The hardware
            # was correct throughout; only the picture was wrong, which is the
            # most awkward kind of bug to notice.
            if name in agent_tools.ACTION_TOOLS and result.get("applied"):
                room, actuator = rooms.parse_actuator_id(args.get("actuator"))
                if room:
                    twin.publish_actuator(
                        room, actuator, "on" if name == "turn_on" else "off")

            messages.append({"role": "tool",
                             "tool_call_id": call.get("id", name),
                             "name": name,
                             "content": json.dumps(result, default=str)})

    print(f"  [warn] reached MAX_HOPS ({MAX_HOPS}) — ending cycle")
    pi_guard.audit({"source": "llm", "tool": "final", "applied": False,
                    "code": "MAX_HOPS",
                    "snapshot_read_at": snapshot.get("read_at")})


# ---------------- fallback ----------------
def run_fallback(building, snapshot):
    """The baseline rule, when the model is unreachable or unusable.

    A building does not stop being controlled because an inference server is
    down. These actions are logged with source="rule" so the analysis can
    separate model decisions from fallback ones instead of silently mixing them.
    """
    for room, data in snapshot["rooms"].items():
        for action in edge_agent_rules.decide(room, data, building.state(room)):
            applied, _, _ = pi_guard.apply(building, action, snapshot,
                                           source="rule")
            if applied:
                _, actuator = rooms.parse_actuator_id(action["actuator"])
                twin.publish_actuator(room, actuator, action["state"])


# ---------------- perception ----------------
def perceive(building, snapshot, read_at):
    """Gather every room's state, and record it if nobody else is.

    Where the two halves come from, and why:

      the PRESENT  the latest value published by pi_sensor.py, held in memory
                   by remote.py. Current, and never waits on a database.
      the PAST     SQLite, which pi_consumer.py fills from the same stream.
                   Older values, a trend, and anything the model asks for with
                   query_history.

    In bus mode the agent must NOT write to the database: pi_consumer.py
    already stored these readings when they came off the bus, and a second
    write would double every row — which would quietly corrupt the trend, the
    recent window and every count derived from them.
    """
    distributed = hasattr(building, "command")

    for room in building.rooms():
        temp  = building.read_temperature(room)
        smoke = building.read_smoke(room)
        if not distributed:
            history.record(room, temp, smoke, read_at)
        lo, hi = rooms.band(room)
        snapshot["rooms"][room] = {
            "temp_c": temp,
            "smoke_v": smoke,
            "comfort_band": [lo, hi],
            "smoke_threshold": rooms.SMOKE_THRESHOLD,
            "recent": history.window(room),
            "trend": history.trend(room),
            **building.state(room),
        }
        # In bus mode pi_sensor.py already published this to the twin; doing
        # it again here would be a second writer for the same value.
        if temp is not None and not hasattr(building, "command"):
            twin.publish_temp(room, temp)


# ---------------- main ----------------
running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

if AGENT_IO == "bus":
    import remote
    try:
        building = remote.get_building()
        print("[io] bus mode — readings from pi_sensor.py, "
              "commands to pi_actuator.py")
    except Exception as exc:
        raise SystemExit(
            f"[io] cannot join the bus: {exc}\n"
            f"     Start the broker and pi_sensor.py / pi_actuator.py, or run\n"
            f"     this agent single-process with AGENT_IO=direct")
else:
    building = hw.get_building()
    print("[io] direct mode — this process owns the sensors and the pins")

twin.register_all()
pi_guard.start_run("llm")

print(f"LLM agent on {rooms.names()}: model={LLM_MODEL} at {LLM_BASE_URL}")
print(f"tool-calling, max {MAX_HOPS} hops/cycle, cycle={CYCLE_S}s, "
      f"history={history.DB_PATH}, audit={pi_guard.AUDIT_PATH}. Ctrl-C to stop.")
print(f"staleness limit {pi_guard.MAX_READING_AGE_S:.0f}s, "
      f"trend window {history.TREND_WINDOW_S:.0f}s")

# Measured once, then checked against the staleness limit. Without this warning
# a too-tight limit refuses every action and the run looks like a model failure
# when it is a configuration one.
_latency_warned = False

cycle = 0
try:
    while running:
        cycle += 1

        # ---- PERCEIVE: read, record, then build the snapshot ----
        #
        # Wrapped because perception touches hardware and a network. One
        # unreadable sensor or a dropped connection must cost a cycle, not the
        # run: an unattended controller that exits on the first surprise walks
        # away leaving a relay in whatever state it happened to be in.
        read_at = time.time()
        snapshot = {"read_at": read_at, "rooms": {}}
        try:
            perceive(building, snapshot, read_at)
        except Exception as exc:
            print(f"  [perceive] failed: {exc} — skipping this cycle")
            pi_guard.audit({"source": "agent", "applied": False,
                            "code": "PERCEIVE_FAILED", "detail": str(exc)})
            time.sleep(CYCLE_S)
            continue

        print(f"\n=== cycle {cycle} {time.strftime('%H:%M:%S')}")
        for room, d in snapshot["rooms"].items():
            t = "n/a" if d["temp_c"] is None else f"{d['temp_c']:.2f}"
            rate = d["trend"].get("smoke_v_per_s")
            print(f"    {room}: {t} C, smoke {d['smoke_v']:.2f} V"
                  f"{'' if rate is None else f' ({rate:+.3f} V/s)'}, "
                  f"heater={d['heater']}, buzzer={d['buzzer']}")

        # ---- DECIDE + GUARD + ACT ----
        started = time.time()
        try:
            run_react(building, snapshot)
            elapsed = time.time() - started
            print(f"  [llm {elapsed:.1f}s]")
            if elapsed > pi_guard.MAX_READING_AGE_S * 0.7 and not _latency_warned:
                print(f"  [WARN] inference takes {elapsed:.0f}s but readings go "
                      f"stale at {pi_guard.MAX_READING_AGE_S:.0f}s — actions "
                      f"will start being refused as H4. "
                      f"Raise MAX_READING_AGE_S or use a smaller model.")
                _latency_warned = True
        except Exception as exc:
            print(f"  [model] failed: {exc} -> falling back to rule")
            pi_guard.audit({"source": "llm", "applied": False,
                            "code": "UNAVAILABLE", "detail": str(exc),
                            "snapshot": snapshot})
            run_fallback(building, snapshot)

        if cycle % 30 == 0:
            history.prune()

        time.sleep(CYCLE_S)
finally:
    for room in building.rooms():
        building.set_buzzer(room, False)
        building.set_heater(room, False)
        twin.publish_actuator(room, "heater", "off")
        twin.publish_actuator(room, "buzzer", "off")
    print("\nstopped — all heaters off, all alarms off")
    print("audit summary:", json.dumps(pi_guard.summarise()))
