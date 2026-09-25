"""
fake_hardware.py — simulated stand-in for real_hardware.py.

Plays the role of the "real world" when no Pi is attached. It exposes the
SAME public methods as real_hardware.RealRoom, so the edge agent runs
unchanged whether hardware is present or not:

    read_temperature() -> float   (deg C)
    read_smoke()       -> float   (volts, ~0.1 clean .. ~3.0 smoky)
    set_heater(on: bool)
    set_buzzer(on: bool)

Swap between fake and real with ONE import line in the edge agent:
    import fake_hardware as hw     # simulation (today)
    import real_hardware as hw     # the Pi     (later)
    room = hw.get_room()
"""
import time
import random


class FakeRoom:
    # --- simple thermal model (proportional approach, tutorial-style) ---
    WARM_TARGET = 25.0     # room settles here when the heater is ON
    OUTDOOR     = 18.0     # room drifts toward here when the heater is OFF
    APPROACH    = 0.2      # fraction of the remaining gap closed per 2 s
    FIRE_RATE   = 3.0      # extra deg C per second while a (test) fire is active

    def __init__(self, start_temp=21.0):
        self.temp = start_temp
        self.heater_on = False
        self.buzzer_on = False
        self.fire = False              # test hook for Scenario B
        self._last = time.time()

    def _advance(self):
        now = time.time()
        dt = now - self._last
        self._last = now
        target = self.WARM_TARGET if self.heater_on else self.OUTDOOR
        frac = self.APPROACH * (dt / 2.0)      # framerate-independent
        self.temp += frac * (target - self.temp)
        if self.fire:
            self.temp += self.FIRE_RATE * dt

    # ---------- shared interface (identical to RealRoom) ----------
    def read_temperature(self):
        self._advance()
        return round(self.temp + random.uniform(-0.05, 0.05), 2)

    def read_smoke(self):
        # clean-air baseline ~0.10 V; a fire drives it high (like the real ADC would read)
        base = 0.10 + random.uniform(-0.02, 0.02)
        return round(3.0 if self.fire else base, 3)

    def set_heater(self, on):
        self.heater_on = bool(on)

    def set_buzzer(self, on):
        self.buzzer_on = bool(on)

    # ---------- fake-only test hooks (NOT part of the real driver) ----------
    def set_fire(self, on):
        """Inject/clear a simulated fire so Scenario B can be tested with no hardware."""
        self.fire = bool(on)


def get_room():
    """Factory so the edge agent can stay driver-agnostic: hw.get_room()."""
    return FakeRoom()


if __name__ == "__main__":
    # quick self-test
    r = get_room()
    r.set_heater(True)
    for _ in range(3):
        time.sleep(1)
        print("temp:", r.read_temperature(), " smoke:", r.read_smoke())
    r.set_fire(True)
    time.sleep(1)
    print("FIRE -> temp:", r.read_temperature(), " smoke:", r.read_smoke())
