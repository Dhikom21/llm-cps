"""
pi_actuator.py — owns the GPIO pins, and is the last gate before them.

    MQTT commands/{room}/{actuator}  ->  pi_actuator.py  ->  the relay, the piezo
                                     ->  MQTT actuators/{room}/{actuator}/state

It does not know BuildSim exists either; pi_twin_bridge.py mirrors the state
topic to the floor plan.

--------------------------------------------------------------------------
Why the shield lives HERE and not in the agent
--------------------------------------------------------------------------
pi_guard used to run inside the agent — the same process as the language
model. That is slightly circular: the thing you do not trust was carrying its
own safety check, and any fault in the agent could in principle bypass it.

Now the guard runs in a different process, behind a message bus, in the only
program that holds the pins. The agent cannot drive a pin at all; it can only
ask. Every request is validated here, by code the agent does not share an
address space with, and refused requests never leave this process.

That turns "the agent checks itself" into a boundary: no fault, bug or
hallucination in the agent can switch a relay that this process declines to
switch.

--------------------------------------------------------------------------
Command format
--------------------------------------------------------------------------
    topic   commands/A109/buzzer
    payload {"state": "on",
             "reason": "A109 smoke_v 3.00 above its threshold of 1.0",
             "snapshot": { ... what the requester believed at the time ... }}

The snapshot travels WITH the command because the guard needs it: freshness
(H4) is judged against when the readings were taken, and misattribution (H5)
against what the other rooms looked like. A command without a snapshot can
still be validated for H1 and H2, but not for those two.

--------------------------------------------------------------------------
Failing safe
--------------------------------------------------------------------------
If the bus is lost, this process stops receiving commands — and an actuator
left in its last state could mean a heater running in an empty building. So a
loss of the bus for longer than COMMAND_TIMEOUT_S drives everything off and
says so. Losing contact with the controller is not a reason to keep heating.

--------------------------------------------------------------------------
Run
--------------------------------------------------------------------------
    export BUILDSIM_URL=http://localhost:9090
    python3 pi_actuator.py

    HW=fake python3 pi_actuator.py      # no Pi attached
"""
import os
import signal
import time

import bus
import pi_guard
import rooms

if os.environ.get("HW", "real") == "fake":
    import fake_hardware as hw
else:
    import real_hardware as hw

# How long without any contact before everything is switched off. Generous
# enough to survive a broker restart, short enough that a dead controller does
# not leave a heater running all afternoon.
COMMAND_TIMEOUT_S = float(os.environ.get("COMMAND_TIMEOUT_S", "180"))

running = True
_last_contact = time.time()
_failsafe_tripped = False


def stop(*_):
    global running
    running = False


def main():
    global _last_contact, _failsafe_tripped

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    actuators = hw.get_actuators()
    pi_guard.start_run("actuator")

    link = bus.Bus("pi-actuator").connect()

    def publish_state(room, actuator):
        """Announce the state, retained, so a late subscriber learns it at once.

        Retained messages matter here: an agent that starts after this process
        would otherwise have no idea whether the heater is on until it next
        changes, and would happily command it on again.
        """
        state = actuators.state(room)[actuator]
        link.publish(bus.state_topic(room, actuator),
                     {"room": room, "actuator": actuator, "state": state,
                      "physical": rooms.is_real(room, actuator)},
                     retain=True)

    def on_command(topic, payload):
        """One command has arrived. Validate it, then maybe act on it."""
        global _last_contact, _failsafe_tripped
        _last_contact = time.time()
        _failsafe_tripped = False

        parts = topic.split("/")
        if len(parts) != 3:
            return
        _, room, actuator = parts

        action = {"actuator": f"{room}/{actuator}",
                  "state": payload.get("state"),
                  "reason": payload.get("reason")}
        snapshot = payload.get("snapshot") or {"read_at": payload.get("ts", 0),
                                               "rooms": {}}

        applied, code, detail = pi_guard.apply(
            actuators, action, snapshot,
            source=payload.get("source", "remote"))

        if applied:
            publish_state(room, actuator)

        # Answer the requester. Sent whether or not the command was allowed:
        # a refusal that looks like silence is indistinguishable from a lost
        # message, and the requester would have no idea it had been stopped.
        link.publish(bus.result_topic(room, actuator), {
            "cmd_id": payload.get("cmd_id"),
            "applied": applied, "code": code, "detail": detail,
            "room": room, "actuator": actuator,
        })

    link.subscribe(bus.ALL_COMMANDS, on_command)

    print(f"actuator process: {bus.ALL_COMMANDS} -> GPIO")
    print(f"shield active ({', '.join(sorted(rooms.ACTUATORS))} on "
          f"{', '.join(rooms.names())}), audit -> {pi_guard.AUDIT_PATH}")
    print(f"fail-safe: everything off after {COMMAND_TIMEOUT_S:.0f}s "
          f"without a command")

    # Announce the starting state so the agent does not have to guess.
    for room in rooms.names():
        for actuator in rooms.ACTUATORS:
            publish_state(room, actuator)

    try:
        while running:
            time.sleep(2.0)
            quiet = time.time() - _last_contact
            if quiet > COMMAND_TIMEOUT_S and not _failsafe_tripped:
                print(f"[FAIL-SAFE] no command for {quiet:.0f}s — "
                      f"switching everything off")
                pi_guard.audit({"source": "actuator", "applied": True,
                                "code": "FAILSAFE",
                                "detail": f"no command for {quiet:.0f}s"})
                for room in rooms.names():
                    actuators.set_heater(room, False)
                    actuators.set_buzzer(room, False)
                    for actuator in rooms.ACTUATORS:
                        publish_state(room, actuator)
                _failsafe_tripped = True
    finally:
        actuators.close()
        for room in rooms.names():
            for actuator in rooms.ACTUATORS:
                try:
                    publish_state(room, actuator)
                except Exception:
                    pass
        link.close()
        print("\nactuator process stopped — all heaters off, all alarms off")


if __name__ == "__main__":
    main()
