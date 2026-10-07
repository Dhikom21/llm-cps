"""
fake_hardware.py — simulated stand-in for real_hardware.py, for several rooms.

Same public interface as RealBuilding, so an agent runs unchanged with no Pi
attached:

    rooms()                     -> list of room names
    read_temperature(room)      -> float, deg C
    read_smoke(room)            -> float, volts
    set_heater(room, on)
    set_buzzer(room, on)
    state(room)                 -> {"heater": "on"|"off", "buzzer": ...}

Swap with one import line:
    import fake_hardware as hw      # simulation
    import real_hardware as hw      # the Pi
    building = hw.get_building()

Smoke is read from the twin here too, exactly as on the Pi, so inject_fire.py
drives both the same way and a simulated run is a faithful rehearsal.
Set SMOKE_LOCAL=1 to keep smoke purely in memory instead, for offline work.
"""
import os
import random
import time

import rooms

try:
    import twin
except Exception:                       # offline, no requests, no BuildSim
    twin = None

SMOKE_LOCAL = os.environ.get("SMOKE_LOCAL", "0") == "1"


class _Room:
    """A crude thermal model. Enough to exercise a controller, and honest
    about being nothing more: no walls, no neighbours, no weather."""

    WARM_TARGET = 25.0     # where it settles with the heater on
    OUTDOOR     = 18.0     # where it drifts with the heater off
    APPROACH    = 0.2      # fraction of the remaining gap closed per 2 s

    def __init__(self, start_temp):
        self.temp = start_temp
        self.heater = False
        self.buzzer = False
        self.smoke = 0.10
        self._last = time.time()

    def advance(self):
        now = time.time()
        dt = now - self._last
        self._last = now
        target = self.WARM_TARGET if self.heater else self.OUTDOOR
        # Scaled by dt so the physics does not depend on how often it is called.
        self.temp += self.APPROACH * (dt / 2.0) * (target - self.temp)


class FakeBuilding:
    def __init__(self):
        # Slightly different starting temperatures, so the two rooms are
        # distinguishable in a snapshot. Identical numbers would make a
        # misattributed command impossible to spot in the log.
        self._rooms = {name: _Room(21.0 + i * 1.5)
                       for i, name in enumerate(rooms.names())}

    # ---------- shared interface (identical to RealBuilding) ----------
    def rooms(self):
        return rooms.names()

    def read_temperature(self, room):
        r = self._rooms[room]
        r.advance()
        return round(r.temp + random.uniform(-0.05, 0.05), 2)

    def read_smoke(self, room):
        if SMOKE_LOCAL or twin is None:
            return round(self._rooms[room].smoke, 3)
        return twin.read_smoke(room)

    def set_heater(self, room, on):
        self._rooms[room].heater = bool(on)

    def set_buzzer(self, room, on):
        self._rooms[room].buzzer = bool(on)

    def state(self, room):
        r = self._rooms[room]
        return {"heater": "on" if r.heater else "off",
                "buzzer": "on" if r.buzzer else "off"}

    def close(self):
        for r in self._rooms.values():
            r.heater = r.buzzer = False

    # ---------- fake-only test hook ----------
    def set_smoke(self, room, volts):
        """Only meaningful with SMOKE_LOCAL=1; otherwise use inject_fire.py."""
        self._rooms[room].smoke = float(volts)


def get_building():
    return FakeBuilding()


# The split processes ask for one half each. A FakeBuilding serves as either —
# it simply has methods the caller does not use. The two processes therefore
# hold SEPARATE simulated state, which is correct: in the real system the
# sensor process cannot see the actuator process's pins either, and learns
# actuator states over the bus like everyone else.
def get_sensors():
    return FakeBuilding()


def get_actuators():
    return FakeBuilding()


if __name__ == "__main__":
    b = get_building()
    for _ in range(3):
        for room in b.rooms():
            print(f"{room}: temp={b.read_temperature(room)} "
                  f"smoke={b.read_smoke(room)} {b.state(room)}")
        time.sleep(1)
