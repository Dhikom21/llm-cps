"""
remote.py — the agent's view of a building it cannot touch.

In the split architecture the agent has no sensors and no pins. It learns the
world from the bus and changes it by asking. This class gives it the same
interface the hardware drivers expose, so nothing above it has to know:

    rooms()                 the configured rooms
    read_temperature(room)  the latest published reading
    read_smoke(room)        the latest published reading
    state(room)             the latest published actuator states
    command(room, actuator, state, reason, snapshot)  -> the shield's verdict

--------------------------------------------------------------------------
Readings are cached, not fetched
--------------------------------------------------------------------------
pi_sensor.py publishes on its own schedule; this keeps the last value it saw.
So read_temperature() returns something a few seconds old rather than taking a
fresh measurement — which is exactly what it is, and the age is carried in the
snapshot so the shield can refuse to act on it if it has gone stale.

A value that has never arrived is None, and the controller refuses to act on
None. A sensor process that is not running therefore produces no control
decisions about that room, rather than decisions based on a guess.

--------------------------------------------------------------------------
Commands are asked for, not performed
--------------------------------------------------------------------------
command() publishes to commands/{room}/{actuator} and waits briefly for the
actuator process to answer on results/{room}/{actuator}. The wait is what
keeps the model's feedback loop intact: it still learns, in the same cycle,
that its request was refused and why.

If no answer arrives, that is reported as a timeout rather than assumed to be
success. An agent that believes it turned on an alarm it did not turn on is
worse than one that knows it failed.
"""
import os
import threading
import time
import uuid

import bus
import rooms

RESULT_TIMEOUT_S = float(os.environ.get("COMMAND_RESULT_TIMEOUT_S", "5.0"))
READING_MAX_AGE_S = float(os.environ.get("READING_MAX_AGE_S", "120"))


class RemoteBuilding:
    def __init__(self, client_id="agent"):
        self._readings = {}        # room -> {"temp_c", "smoke_v", "ts"}
        self._states = {}          # (room, actuator) -> "on" | "off"
        self._results = {}         # cmd_id -> result dict
        self._event = threading.Event()
        self._lock = threading.Lock()

        self._bus = bus.Bus(client_id).connect()
        self._bus.subscribe(bus.ALL_READINGS, self._on_reading)
        self._bus.subscribe(bus.ALL_STATES, self._on_state)
        self._bus.subscribe(bus.ALL_RESULTS, self._on_result)

    # ---------------- incoming ----------------
    def _on_reading(self, topic, payload):
        room = payload.get("room")
        if room:
            with self._lock:
                self._readings[room] = payload

    def _on_state(self, topic, payload):
        room, actuator = payload.get("room"), payload.get("actuator")
        if room and actuator:
            with self._lock:
                self._states[(room, actuator)] = payload.get("state", "off")

    def _on_result(self, topic, payload):
        cmd_id = payload.get("cmd_id")
        if cmd_id:
            with self._lock:
                self._results[cmd_id] = payload
            self._event.set()

    # ---------------- the shared interface ----------------
    def rooms(self):
        return rooms.names()

    def _fresh(self, room):
        """The last reading for a room, or None if there is none recent.

        An old reading is dropped rather than returned, because a stopped
        sensor process would otherwise have the agent controlling a room from
        a value that stopped changing an hour ago — which looks exactly like a
        stable room.
        """
        with self._lock:
            r = self._readings.get(room)
        if not r:
            return None
        if time.time() - r.get("ts", 0) > READING_MAX_AGE_S:
            return None
        return r

    def read_temperature(self, room):
        r = self._fresh(room)
        return None if r is None else r.get("temp_c")

    def read_smoke(self, room):
        r = self._fresh(room)
        # 0.10 is clean air. Unlike temperature, a missing smoke reading is
        # reported as clean rather than None, because the comfort logic must
        # keep working when only the fire half is unavailable. It is the same
        # fail-quiet trade-off the single-process driver makes, and it has the
        # same caveat: an outage will not raise an alarm.
        return 0.10 if r is None else r.get("smoke_v", 0.10)

    def reading_age(self, room):
        with self._lock:
            r = self._readings.get(room)
        return None if not r else time.time() - r.get("ts", 0)

    def state(self, room):
        with self._lock:
            return {a: self._states.get((room, a), "off")
                    for a in rooms.ACTUATORS}

    # ---------------- outgoing ----------------
    def command(self, room, actuator, state, reason, snapshot):
        """Ask the actuator process to change something. Returns its verdict."""
        cmd_id = uuid.uuid4().hex
        self._event.clear()
        self._bus.publish(bus.command_topic(room, actuator), {
            "cmd_id": cmd_id, "state": state, "reason": reason,
            "snapshot": snapshot, "source": "llm",
        })

        deadline = time.time() + RESULT_TIMEOUT_S
        while time.time() < deadline:
            with self._lock:
                result = self._results.pop(cmd_id, None)
            if result is not None:
                return result
            self._event.wait(0.1)
            self._event.clear()

        return {"applied": False, "code": "NO_RESPONSE",
                "detail": f"the actuator process did not answer within "
                          f"{RESULT_TIMEOUT_S:.0f}s — is pi_actuator.py running?"}

    def close(self):
        self._bus.close()


def get_building():
    return RemoteBuilding()
