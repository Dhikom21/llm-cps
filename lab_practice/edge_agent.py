"""
edge_agent.py — the rule-based baseline, now across several rooms.

This is the control the LLM version is measured against, so it is deliberately
dull: a handful of comparisons that cannot be wrong. Everything interesting in
the project is a deviation from what this file does.

    PERCEIVE   real DS18B20 per room  +  smoke from the twin
    DECIDE     a rule, per room
    GUARD      every action goes through pi_guard, same as the LLM version
    ACT        A109's relay and piezo are real; A108's actuators are virtual
    PUBLISH    readings and states to BuildSim

Running the baseline through the same guard is the point. Both agents produce
the same shape of audit record, so the two runs are directly comparable and the
rule's row in the results table is a measurement rather than an assumption.

--------------------------------------------------------------------------
Safety ordering
--------------------------------------------------------------------------
Smoke beats temperature. Above threshold the heater is forced off and the
buzzer on, however cold the room is. It is the first branch in decide() so the
precedence is visible and reviewable — and so the LLM can be tested on whether
it respects the same ordering.

--------------------------------------------------------------------------
Run
--------------------------------------------------------------------------
    export BUILDSIM_URL=http://localhost:9090
    export BAND_LO=24 BAND_HI=25          # for a hand-warming demo
    python3 edge_agent.py
"""
import json
import os
import signal
import time

import edge_agent_rules
import pi_guard
import rooms
import twin

if os.environ.get("HW", "real") == "fake":
    import fake_hardware as hw
else:
    import real_hardware as hw

CYCLE_S = float(os.environ.get("CYCLE_S", "2.0"))

# The decision lives in its own module so the LLM agent can fall back to THIS
# code rather than a second copy that might quietly drift out of step.
decide = edge_agent_rules.decide


# ---------------- main ----------------
running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

building = hw.get_building()
twin.register_all()

print(f"edge agent (rule) on {rooms.names()}: "
      f"band {rooms.DEFAULT_BAND}, smoke threshold {rooms.SMOKE_THRESHOLD} V, "
      f"cycle {CYCLE_S}s, audit -> {pi_guard.AUDIT_PATH}. Ctrl-C to stop.")

last_line = {}

try:
    while running:
        # ---- PERCEIVE: one snapshot covering every room ----
        read_at = time.time()
        snapshot = {"read_at": read_at, "rooms": {}}

        for room in building.rooms():
            temp = building.read_temperature(room)
            lo, hi = rooms.band(room)
            snapshot["rooms"][room] = {
                "temp_c": temp,
                "smoke_v": building.read_smoke(room),
                "comfort_band": [lo, hi],
                "smoke_threshold": rooms.SMOKE_THRESHOLD,
                **building.state(room),
            }
            if temp is not None:
                twin.publish_temp(room, temp)

        # ---- DECIDE + GUARD + ACT, room by room ----
        for room, data in snapshot["rooms"].items():
            state = building.state(room)

            for action in decide(room, data, state):
                applied, _, _ = pi_guard.apply(building, action, snapshot,
                                               source="rule")
                if applied:
                    _, actuator = rooms.parse_actuator_id(action["actuator"])
                    twin.publish_actuator(room, actuator, action["state"])

            # Print only when something about the room changes, so a loop
            # running every two seconds does not bury the lines that matter.
            t = "  n/a" if data["temp_c"] is None else f"{data['temp_c']:6.2f}"
            line = (f"{room}  {t} C  smoke {data['smoke_v']:.2f} V  "
                    f"heater={building.state(room)['heater']:<3} "
                    f"alarm={building.state(room)['buzzer']}")
            if line != last_line.get(room):
                print(f"{time.strftime('%H:%M:%S')}  {line}")
                last_line[room] = line

        time.sleep(CYCLE_S)
finally:
    for room in building.rooms():
        building.set_buzzer(room, False)
        building.set_heater(room, False)
        twin.publish_actuator(room, "heater", "off")
        twin.publish_actuator(room, "buzzer", "off")
    print("\nstopped — all heaters off, all alarms off")
    print("audit summary:", json.dumps(pi_guard.summarise()))
