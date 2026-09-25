"""
Level 8 — Multi-agent coordination (lecture-4 §Multi-Agent Coordination)

Three independent agents propose actions; a priority-based coordinator
resolves conflicts; only one command per cycle reaches BuildSim.

Agents implemented:

  SafetyAgent   priority 1000 — fires if temp spikes above hard limit (>30°C)
                                 → must turn heater OFF immediately.
                                 Always wins (lecture-4: "safety always overrides").

  ComfortAgent  priority   20 — keeps temp inside comfort band 20-23 °C
                                 (the level-5 rule, made first-class).

  EnergyAgent   priority   10 — wants the heater OFF when possible to save energy;
                                 only fires if temperature is "high enough" (>22.5 C).

Each agent emits a Proposal:
    Proposal(agent_id, priority, actuator_id, state, reason)

The coordinator gathers all Proposals each tick, picks the highest-priority
non-conflicting set, applies hysteresis (lecture-4 §Avoiding Pathologies:
no actuator may flip more than once every HYSTERESIS_S seconds), and submits
the chosen commands via the level-7 tool registry (which audits + validates).

Pre-requisites:
  BuildSim, Mosquitto, TimescaleDB, level4_sensor.py, level4_consumer.py
  python level7_react_agent.py   ← stop this if it's running; we replace it.

Run:
  python level8_multi_agent.py
"""
import time
import signal
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

import db
import level7_tools as tools


HEATER_ID   = "level4-heater-A109-state"
ROOM        = "A109"
CYCLE_S     = 4.0
HYSTERESIS_S = 15.0     # an actuator may not change state more than once per this
WINDOW_S    = 30        # how far back each agent looks


# ---------- data types ----------

@dataclass
class Proposal:
    agent_id:    str
    priority:    int
    actuator_id: str
    state:       str
    reason:      str

    def __repr__(self) -> str:
        return (f"<{self.agent_id} p={self.priority} "
                f"{self.actuator_id}={self.state!r} :: {self.reason}>")


# ---------- agents ----------

def perceive_temperature() -> Optional[Dict]:
    res = tools.call("coordinator", "read_sensors",
                     {"room": ROOM, "sensor_types": ["temperature"],
                      "last_seconds": WINDOW_S})
    if not res.success or res.data.get("no_data"):
        return None
    return res.data["sensors"].get("temperature")


class SafetyAgent:
    AGENT_ID = "safety-A109"
    PRIORITY = 1000
    UPPER_HARD_LIMIT = 30.0

    def propose(self, temp: Dict) -> Optional[Proposal]:
        max_t = temp["max"]
        cur_t = temp["current"]
        # Fires whenever current OR recent max breaches the hard limit
        if max_t >= self.UPPER_HARD_LIMIT or cur_t >= self.UPPER_HARD_LIMIT:
            tools._audit(self.AGENT_ID, "thought",
                         text=f"max={max_t} cur={cur_t} hits hard limit "
                              f"{self.UPPER_HARD_LIMIT}")
            return Proposal(self.AGENT_ID, self.PRIORITY,
                            HEATER_ID, "off",
                            f"safety: temp {max_t:.2f}>={self.UPPER_HARD_LIMIT} hard limit")
        return None


class ComfortAgent:
    AGENT_ID = "comfort-A109"
    PRIORITY = 20
    BAND_LO  = 20.0
    BAND_HI  = 23.0

    def propose(self, temp: Dict) -> Optional[Proposal]:
        avg = temp["mean"]
        if avg < self.BAND_LO:
            return Proposal(self.AGENT_ID, self.PRIORITY,
                            HEATER_ID, "on",
                            f"comfort: avg {avg:.2f} below band {self.BAND_LO}")
        if avg > self.BAND_HI:
            return Proposal(self.AGENT_ID, self.PRIORITY,
                            HEATER_ID, "off",
                            f"comfort: avg {avg:.2f} above band {self.BAND_HI}")
        return None


class EnergyAgent:
    AGENT_ID = "energy-A109"
    PRIORITY = 10
    OFF_THRESHOLD = 22.5

    def propose(self, temp: Dict) -> Optional[Proposal]:
        avg = temp["mean"]
        # Wants the heater off any time we're above 22.5 C — comfort agent (priority 20)
        # will outvote this if it actually wants heater on, but if comfort is happy
        # mid-band and energy wants off, energy wins by default.
        if avg > self.OFF_THRESHOLD:
            return Proposal(self.AGENT_ID, self.PRIORITY,
                            HEATER_ID, "off",
                            f"energy: avg {avg:.2f} > {self.OFF_THRESHOLD} — heater off saves energy")
        return None


# ---------- coordinator ----------

class Coordinator:
    """Priority-based conflict resolution with anti-oscillation hysteresis.

    The lecture (§Avoiding Pathologies) names three failure modes; we mitigate
    the most common one — OSCILLATION — by refusing to flip an actuator more
    often than once per HYSTERESIS_S seconds. Deadlock isn't possible here
    because no agent holds locks. Thrashing is prevented by the same rate
    limit.
    """

    def __init__(self):
        self.agents = [SafetyAgent(), ComfortAgent(), EnergyAgent()]
        self.last_command: Dict[str, str]   = {}   # actuator_id -> last state
        self.last_change_ts: Dict[str, float] = {} # actuator_id -> wall-clock ts

    def tick(self):
        temp = perceive_temperature()
        if temp is None:
            print(f"  [coordinator] no data — skipping")
            return

        # 1. gather proposals
        proposals: List[Proposal] = []
        for ag in self.agents:
            p = ag.propose(temp)
            if p is not None:
                proposals.append(p)
                print(f"  proposal {p}")

        if not proposals:
            print(f"  [coordinator] all agents quiet — keep current state")
            return

        # 2. resolve conflicts per actuator: highest priority wins
        winners: Dict[str, Proposal] = {}
        for p in proposals:
            cur = winners.get(p.actuator_id)
            if cur is None or p.priority > cur.priority:
                winners[p.actuator_id] = p

        # 3. apply with hysteresis
        now = time.time()
        for actuator_id, winner in winners.items():
            # if state isn't changing, do nothing
            if self.last_command.get(actuator_id) == winner.state:
                continue
            # hysteresis: refuse if last change was too recent
            last_ts = self.last_change_ts.get(actuator_id, 0)
            since = now - last_ts
            if since < HYSTERESIS_S:
                wait = HYSTERESIS_S - since
                print(f"  [hysteresis] refusing {actuator_id}={winner.state!r} "
                      f"({wait:.1f}s left); proposed by {winner.agent_id}")
                tools._audit("coordinator", "guardrail_blocked",
                             text=f"hysteresis: refuse {actuator_id}={winner.state} "
                                  f"for {wait:.1f}s more",
                             success=False)
                continue

            print(f"  [APPLY] {winner}")
            result = tools.call("coordinator", "set_actuator", {
                "actuator_id": actuator_id,
                "state":       winner.state,
                "reason":      f"({winner.agent_id} p={winner.priority}) {winner.reason}",
            })
            if result.success:
                self.last_command[actuator_id]   = winner.state
                self.last_change_ts[actuator_id] = now
            else:
                print(f"  [APPLY FAILED] {result.error}")


# ---------- main ----------

def cleanup(*_):
    print("\nstopping multi-agent.")
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

print(f"multi-agent system starting. agents=[safety, comfort, energy]")
print(f"cycle={CYCLE_S}s, hysteresis={HYSTERESIS_S}s, room={ROOM}")
db.ensure_schema()

coord = Coordinator()
n = 0
while True:
    n += 1
    print(f"\n=== tick {n} === {time.strftime('%H:%M:%S')}")
    try:
        coord.tick()
    except Exception as e:
        print(f"  [error] tick crashed: {e}")
    time.sleep(CYCLE_S)
