"""
real_hardware.py — drives the ACTUAL Raspberry Pi hardware.

Exposes the same public methods as fake_hardware.FakeRoom, so swapping the
edge agent from simulation to hardware is a single import change:

    import real_hardware as hw
    room = hw.get_room()

--------------------------------------------------------------------------
HYBRID SENSING — why smoke is not read from a pin
--------------------------------------------------------------------------
Temperature and both actuators are REAL, on this Pi.

Smoke is NOT. The SEN0570 smoke sensor has an ANALOG output and the
Raspberry Pi has no analog inputs at all, so reading it needs an external
ADC (ADS1115) that we do not have. Rather than stub the value out, this
driver reads the smoke level from the BuildSim digital twin over REST.

That gives the project a genuine cross-layer path:

    simulated fire in the twin  ->  read_smoke() here  ->  REAL buzzer beeps

A cyber-side event producing a physical effect on the desk. It is also the
setup for the H-taxonomy experiments: the agent can be induced to sound a
real alarm from a fabricated reading.

--------------------------------------------------------------------------
One-time Pi setup
--------------------------------------------------------------------------
    sudo raspi-config nonint do_onewire 0      # enable 1-Wire
    sudo reboot
    sudo apt install -y python3-gpiozero python3-w1thermsensor
    pip install requests --break-system-packages

--------------------------------------------------------------------------
Wiring as actually built (see CPS_Pin_Wiring.png)
--------------------------------------------------------------------------
    DS18B20 x2:  DATA -> GPIO4 (pin 7) | VDD -> 3.3V (pin 1) | GND -> pin 9
                 one 4.7k resistor between DATA and VDD for the whole bus
    Relay:       SIG  -> GPIO17 (pin 11) | VCC -> 5V (pin 2) | GND -> pin 6
    Piezo:       leg1 -> GPIO27 (pin 13) | leg2 -> GND (pin 20)
                 driven with PWM - a steady HIGH is silent on a piezo
    Smoke:       not wired (see above) - comes from BuildSim

--------------------------------------------------------------------------
Pointing the Pi at BuildSim
--------------------------------------------------------------------------
BuildSim runs on the laptop, not on the Pi, so localhost is wrong here.
Set the laptop's address on the project network before running:

    export BUILDSIM_URL=http://192.168.1.44:9090
    python3 real_hardware.py

If BuildSim is unreachable the driver degrades gracefully: read_smoke()
returns the clean-air baseline and warns once, so the control loop keeps
running on real temperature instead of crashing.
"""
import os
import time

import requests
from gpiozero import OutputDevice, PWMOutputDevice
from w1thermsensor import W1ThermSensor

# ---------------- pins ----------------
HEATER_PIN = 17          # BCM numbering (physical pin 11)
BUZZER_PIN = 27          # BCM numbering (physical pin 13)
BUZZER_HZ  = 2000        # tone frequency; a piezo needs switching, not DC

# ---------------- BuildSim (smoke source) ----------------
BUILDSIM     = os.environ.get("BUILDSIM_URL", "http://localhost:9090")
SMOKE_EQ_ID  = "pi-smoke-A109"
SMOKE_VAL_ID = "pi-smoke-A109-val"
ROOM, LEVEL  = "A109", "level0"

CLEAN_AIR_V  = 0.10      # baseline volts in clean air, matching fake_hardware
HTTP_TIMEOUT = 2.0


class RealRoom:
    def __init__(self):
        # --- actuators: both real ---
        self._heater = OutputDevice(HEATER_PIN, active_high=True, initial_value=False)
        self._buzzer = PWMOutputDevice(BUZZER_PIN, frequency=BUZZER_HZ, initial_value=0)

        # --- temperature: real, 1-Wire ---
        self._sensors = W1ThermSensor.get_available_sensors()
        if not self._sensors:
            raise RuntimeError(
                "no DS18B20 found on the 1-Wire bus. Check: ls /sys/bus/w1/devices/ "
                "(you should see one or more 28-* entries)")
        print(f"[hw] {len(self._sensors)} DS18B20 found: "
              f"{[s.id for s in self._sensors]}")

        # --- smoke: from the digital twin ---
        self._twin_warned = False
        self._register_smoke_in_twin()

    # ---------- BuildSim plumbing ----------
    def _register_smoke_in_twin(self):
        """Create the smoke sensor in BuildSim if it isn't there yet, so the
        twin always has something to read and inject_fire.py has a target."""
        try:
            requests.post(f"{BUILDSIM}/api/equipment", timeout=HTTP_TIMEOUT, json={
                "id": SMOKE_EQ_ID, "name": "Pi Smoke (simulated)",
                "type": "smoke_sensor", "category": "safety",
                "level": LEVEL, "room": ROOM, "status": "running"})
            requests.post(f"{BUILDSIM}/api/equipment/{SMOKE_EQ_ID}/sensors",
                          timeout=HTTP_TIMEOUT, json={
                              "id": SMOKE_VAL_ID, "name": "Smoke",
                              "type": "smoke", "data_type": "text",
                              "unit": "V", "value": f"{CLEAN_AIR_V:.3f}"})
            requests.post(f"{BUILDSIM}/api/equipment/notify", timeout=HTTP_TIMEOUT)
            print(f"[hw] smoke sensor registered in twin at {BUILDSIM}")
        except requests.RequestException as e:
            self._warn_twin(e)

    def _warn_twin(self, err):
        if not self._twin_warned:
            print(f"[hw] BuildSim unreachable at {BUILDSIM} ({err}) — "
                  f"smoke will report clean air. Set BUILDSIM_URL to the "
                  f"laptop's IP on the project network.")
            self._twin_warned = True

    # ---------- shared interface (identical to FakeRoom) ----------
    def read_temperature(self):
        """Degrees C from the first DS18B20. REAL."""
        return round(self._sensors[0].get_temperature(), 2)

    def read_smoke(self):
        """Volts, higher = more smoke. Comes from the BuildSim twin, not a pin."""
        try:
            r = requests.get(f"{BUILDSIM}/api/equipment/{SMOKE_EQ_ID}",
                             timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            for s in r.json().get("sensors", []):
                if s.get("id") == SMOKE_VAL_ID:
                    return round(float(s.get("value", CLEAN_AIR_V)), 3)
        except (requests.RequestException, ValueError, TypeError) as e:
            self._warn_twin(e)
        return CLEAN_AIR_V

    def set_heater(self, on):
        """REAL — the relay clicks."""
        self._heater.on() if on else self._heater.off()

    def set_buzzer(self, on):
        """REAL — PWM at 50% duty makes the piezo sound; 0 silences it."""
        self._buzzer.value = 0.5 if on else 0.0

    # ---------- extras (not part of the shared interface) ----------
    def read_all_temperatures(self):
        """Every DS18B20 as {sensor_id: degC} — useful once both are placed."""
        return {s.id: round(s.get_temperature(), 2) for s in self._sensors}

    def close(self):
        """Leave the hardware safe. Always call this on the way out."""
        self._buzzer.value = 0.0
        self._heater.off()


def get_room():
    """Factory so the edge agent can stay driver-agnostic: hw.get_room()."""
    return RealRoom()


if __name__ == "__main__":
    r = get_room()
    try:
        print("temps:", r.read_all_temperatures())

        print("Heater ON for 5 s (listen for the relay click) ...")
        r.set_heater(True); time.sleep(5); r.set_heater(False)
        print("Heater OFF")

        print("temp:", r.read_temperature(), " smoke:", r.read_smoke())

        print("Buzzer beep ...")
        r.set_buzzer(True); time.sleep(1); r.set_buzzer(False)

        print("self-test OK")
    finally:
        r.close()
