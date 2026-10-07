"""
real_hardware.py — drives the ACTUAL Raspberry Pi hardware, for several rooms.

Same public interface as fake_hardware.FakeBuilding, so the agents move between
simulation and reality with one import line.

--------------------------------------------------------------------------
What is real and what is not
--------------------------------------------------------------------------
    A109   temperature: REAL DS18B20   heater: REAL relay   buzzer: REAL piezo
    A108   temperature: REAL DS18B20   heater: virtual      buzzer: virtual
    both   smoke:       from the twin (no ADC on the Pi)

Two real sensors share one 1-Wire bus; rooms.py maps each room to a specific
64-bit sensor ID rather than a position in a list, so a rewire cannot silently
swap the rooms.

A108's actuators are virtual: state is tracked and mirrored to the twin, but
nothing physically moves. That asymmetry is deliberate — see rooms.py.

--------------------------------------------------------------------------
Pi setup
--------------------------------------------------------------------------
    sudo raspi-config nonint do_onewire 0
    sudo reboot
    sudo apt install -y python3-gpiozero python3-w1thermsensor
    pip install requests --break-system-packages

Wiring as built:
    DS18B20 x2   DATA -> GPIO4 (pin 7), VDD -> 3.3V (pin 1), GND -> pin 9
                 one 4.7k pull-up between DATA and VDD for the whole bus
    Relay        SIG  -> GPIO17 (pin 11), VCC -> 5V (pin 2), GND -> pin 6
    Piezo        leg  -> GPIO27 (pin 13), leg -> GND (pin 20)
                 driven with PWM; a steady HIGH is silent on a piezo
"""
import time

from gpiozero import OutputDevice, PWMOutputDevice
from w1thermsensor import W1ThermSensor

import rooms
import twin

HEATER_PIN = 17          # BCM (physical pin 11)
BUZZER_PIN = 27          # BCM (physical pin 13)
BUZZER_HZ  = 2000        # a piezo needs switching, not DC


class Sensors:
    """Owns the 1-Wire bus. Touches no GPIO output, claims no pins.

    Separated from the actuators so the sensor process and the actuator
    process can run independently. gpiozero claims an output pin exclusively,
    so a single class owning both would mean only one process could ever exist.
    """

    def __init__(self):
        found = {s.id: s for s in W1ThermSensor.get_available_sensors()}
        if not found:
            raise RuntimeError(
                "no DS18B20 on the 1-Wire bus. Check: ls /sys/bus/w1/devices/ "
                "(you should see one or more 28-* entries)")

        self._sensors = {}
        for room in rooms.names():
            sid = rooms.ROOMS[room]["sensor_id"]
            if sid in found:
                self._sensors[room] = found[sid]
            else:
                print(f"[hw] WARNING: {room} expects sensor {sid}, not found. "
                      f"Available: {sorted(found)}. "
                      f"Set SENSOR_{room}=<id> to correct this.")
        print(f"[hw] sensors mapped: "
              f"{ {r: s.id for r, s in self._sensors.items()} }")
        self._faults = {}

    def rooms(self):
        return rooms.names()

    def read_temperature(self, room):
        """REAL. Returns None when the reading cannot be trusted.

        None is honest: the controller refuses to act on it, whereas a
        made-up number would be acted upon.

        Every failure mode of a 1-Wire sensor is caught here. A DS18B20 that
        briefly loses power returns its reset value of 85 °C and the library
        raises; a sensor can also vanish from the bus mid-run, or fail CRC.
        None of those is a reason to stop controlling the other room.
        """
        sensor = self._sensors.get(room)
        if sensor is None:
            return None
        try:
            value = round(sensor.get_temperature(), 2)
        except Exception as exc:
            self._sensor_fault(room, exc)
            return None
        self._faults.pop(room, None)          # it is reading again
        return value

    def read_smoke(self, room):
        """From the twin. No ADC on the Pi, so this is the simulated value."""
        return twin.read_smoke(room)

    def _sensor_fault(self, room, exc):
        """Complain once per fault, not once per cycle."""
        first = room not in self._faults
        self._faults[room] = self._faults.get(room, 0) + 1
        if first:
            print(f"[hw] {room} sensor unreadable: {type(exc).__name__}: {exc}")
            if "85" in str(exc):
                print(f"[hw] 85 °C is the DS18B20 power-on reset value — "
                      f"check {room}'s VDD and GND jumpers and the pull-up")

    def sensor_faults(self):
        return dict(self._faults)

    def close(self):
        pass                                   # nothing claimed, nothing to free


class Actuators:
    """Owns the GPIO pins, and is the only thing in the system that does.

    One relay and one piezo exist, and A109 owns them. A108's actuators are
    virtual: state is tracked and mirrored to the twin, but nothing moves.
    """

    def __init__(self):
        self._heater = OutputDevice(HEATER_PIN, active_high=True,
                                    initial_value=False)
        self._buzzer = PWMOutputDevice(BUZZER_PIN, frequency=BUZZER_HZ,
                                       initial_value=0)
        self._state = {room: {"heater": False, "buzzer": False}
                       for room in rooms.names()}
        print(f"[hw] actuators claimed: GPIO{HEATER_PIN} relay, "
              f"GPIO{BUZZER_PIN} piezo")

    def rooms(self):
        return rooms.names()

    def set_heater(self, room, on):
        self._state[room]["heater"] = bool(on)
        if rooms.is_real(room, "heater"):
            self._heater.on() if on else self._heater.off()

    def set_buzzer(self, room, on):
        self._state[room]["buzzer"] = bool(on)
        if rooms.is_real(room, "buzzer"):
            self._buzzer.value = 0.5 if on else 0.0

    def state(self, room):
        s = self._state[room]
        return {"heater": "on" if s["heater"] else "off",
                "buzzer": "on" if s["buzzer"] else "off"}

    def close(self):
        """Leave the hardware safe. A variable disappears when the process
        ends; an energised relay does not."""
        self._buzzer.value = 0.0
        self._heater.off()
        for room in rooms.names():
            self._state[room] = {"heater": False, "buzzer": False}


class RealBuilding(Sensors, Actuators):
    """Both halves in one object, for the single-process agents.

    The split processes use Sensors and Actuators separately; this composes
    them so edge_agent.py and the direct-mode LLM agent keep working unchanged.
    """

    def __init__(self):
        Sensors.__init__(self)
        Actuators.__init__(self)
        for room in rooms.names():
            twin.register_room(room)

    def read_all_temperatures(self):
        """Every DS18B20 as {sensor_id: degC}."""
        return {s.id: round(s.get_temperature(), 2)
                for s in self._sensors.values()}

    def close(self):
        Actuators.close(self)


def get_sensors():
    """For pi_sensor.py — the 1-Wire bus only."""
    return Sensors()


def get_actuators():
    """For pi_actuator.py — the GPIO pins only."""
    return Actuators()


def get_building():
    return RealBuilding()


if __name__ == "__main__":
    b = get_building()
    try:
        for room in b.rooms():
            print(f"{room}: temp={b.read_temperature(room)} "
                  f"smoke={b.read_smoke(room)} state={b.state(room)}")

        print("\nA109 heater ON for 5 s (listen for the relay click) ...")
        b.set_heater("A109", True); time.sleep(5); b.set_heater("A109", False)

        print("A109 buzzer beep ...")
        b.set_buzzer("A109", True); time.sleep(1); b.set_buzzer("A109", False)

        print("A108 heater ON (virtual — nothing should click) ...")
        b.set_heater("A108", True); time.sleep(1)
        print("A108 state:", b.state("A108"))
        b.set_heater("A108", False)

        print("self-test OK")
    finally:
        b.close()
