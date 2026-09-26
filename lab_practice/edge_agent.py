"""
edge_agent.py — the Scenario A control loop, running ON the Raspberry Pi.

This is the milestone where the project stops being a simulation. The loop is
the classic CPS cycle, with real hardware on both ends:

    PERCEIVE   real DS18B20 temperature  +  smoke level from the BuildSim twin
    DECIDE     a plain rule (no LLM yet — that is the next milestone)
    ACT        real relay (heater) and real piezo (alarm)
    PUBLISH    every reading to BuildSim (live view) and MQTT (history)

Deliberately rule-based. It is the baseline the LLM agent gets compared
against, so it has to be simple enough to be obviously correct.

--------------------------------------------------------------------------
Safety override
--------------------------------------------------------------------------
Smoke beats temperature. If the twin reports smoke above threshold, the
heater is forced OFF and the buzzer sounds, whatever the temperature says.
Comfort never outranks safety — and this ordering is explicit here so that
the LLM version can later be tested on whether it respects it.

--------------------------------------------------------------------------
Run
--------------------------------------------------------------------------
On the Pi:
    export BUILDSIM_URL=http://192.168.1.44:9090     # the laptop
    export MQTT_HOST=192.168.1.44
    python3 edge_agent.py

To test the fire path, from the laptop:
    python3 inject_fire.py ramp

Switch back to a simulated room with one line below (HW import).
"""
import os
import signal
import sys
import time

import requests

# --- the one line that chooses simulation vs reality ---
import real_hardware as hw            # the Pi
# import fake_hardware as hw          # no hardware attached

# ---------------- configuration ----------------
BUILDSIM  = os.environ.get("BUILDSIM_URL", "http://localhost:9090")
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

ROOM, LEVEL = "A109", "level0"
TEMP_EQ_ID, TEMP_VAL_ID = "pi-temp-A109", "pi-temp-A109-val"
HEAT_EQ_ID, HEAT_ST_ID  = "pi-heater-A109", "pi-heater-A109-state"
TOPIC = f"sensors/{LEVEL}/{ROOM}/temperature"

# comfort band with hysteresis — heat below LO, stop above HI.
# The gap between them is what stops the relay chattering around a setpoint.
BAND_LO, BAND_HI = 22.0, 24.0
SMOKE_THRESHOLD  = 1.0        # volts; clean air sits near 0.1
CYCLE_S          = 2.0
HTTP_TIMEOUT     = 2.0

# ---------------- optional MQTT ----------------
try:
    import paho.mqtt.client as mqtt
    _mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pi-edge-agent")
    _mqtt.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    _mqtt.loop_start()
    print(f"[mqtt] connected to {MQTT_HOST}:{MQTT_PORT}, topic={TOPIC}")
except Exception as e:                      # broker down, or paho not installed
    _mqtt = None
    print(f"[mqtt] unavailable ({e}) — continuing without history publishing")


# ---------------- BuildSim plumbing ----------------
def register():
    """Announce this Pi's sensor and actuator to the twin, once at startup."""
    try:
        requests.post(f"{BUILDSIM}/api/equipment", timeout=HTTP_TIMEOUT, json={
            "id": TEMP_EQ_ID, "name": "Pi Temperature (real)",
            "type": "temperature_sensor", "category": "monitoring",
            "level": LEVEL, "room": ROOM, "status": "running"})
        requests.post(f"{BUILDSIM}/api/equipment/{TEMP_EQ_ID}/sensors",
                      timeout=HTTP_TIMEOUT, json={
                          "id": TEMP_VAL_ID, "name": "Temperature",
                          "type": "temperature", "data_type": "text",
                          "unit": "C", "value": "0.00"})

        requests.post(f"{BUILDSIM}/api/equipment", timeout=HTTP_TIMEOUT, json={
            "id": HEAT_EQ_ID, "name": "Pi Heater (real relay)",
            "type": "heater", "category": "hvac",
            "level": LEVEL, "room": ROOM, "status": "running"})
        requests.post(f"{BUILDSIM}/api/equipment/{HEAT_EQ_ID}/actuators",
                      timeout=HTTP_TIMEOUT, json={
                          "id": HEAT_ST_ID, "name": "State",
                          "type": "state", "data_type": "text", "value": "off"})

        requests.post(f"{BUILDSIM}/api/equipment/notify", timeout=HTTP_TIMEOUT)
        print(f"[twin] registered temp + heater at {BUILDSIM}")
    except requests.RequestException as e:
        print(f"[twin] registration failed ({e}) — running local-only")


def publish_temp(value):
    """Two writes per reading: the twin for the live view, MQTT for history."""
    try:
        requests.put(f"{BUILDSIM}/api/sensors/{TEMP_VAL_ID}/value",
                     json={"data_type": "text", "value": f"{value:.2f}"},
                     timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        pass                                    # the twin is a view, not the loop
    if _mqtt:
        _mqtt.publish(TOPIC, f'{{"room":"{ROOM}","value":{value:.2f},'
                             f'"ts":{time.time():.0f}}}')


def publish_heater(state):
    try:
        requests.put(f"{BUILDSIM}/api/actuators/{HEAT_ST_ID}/state",
                     json={"data_type": "text", "value": state},
                     timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        pass


# ---------------- the decision ----------------
def decide(temp, smoke, heater_on):
    """Return (heater_on, buzzer_on, why). Pure function — easy to unit-test,
    and the exact job the LLM takes over in the next milestone."""
    if smoke >= SMOKE_THRESHOLD:
        return False, True, f"FIRE: smoke {smoke:.2f} V >= {SMOKE_THRESHOLD}"
    if temp < BAND_LO:
        return True, False, f"temp {temp:.2f} below {BAND_LO}"
    if temp > BAND_HI:
        return False, False, f"temp {temp:.2f} above {BAND_HI}"
    return heater_on, False, "within band, hold"


# ---------------- main loop ----------------
room = hw.get_room()
register()

running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

print(f"edge agent running: band {BAND_LO}-{BAND_HI} C, cycle {CYCLE_S}s. "
      f"Ctrl-C to stop.")

heater_on = False
last_reason = None

try:
    while running:
        # PERCEIVE
        temp  = room.read_temperature()
        smoke = room.read_smoke()

        # DECIDE
        want_heat, want_buzz, why = decide(temp, smoke, heater_on)

        # ACT — only when the state actually changes
        if want_heat != heater_on:
            room.set_heater(want_heat)
            heater_on = want_heat
            publish_heater("on" if heater_on else "off")
        room.set_buzzer(want_buzz)

        # PUBLISH
        publish_temp(temp)

        if why != last_reason:
            print(f"{time.strftime('%H:%M:%S')}  {temp:6.2f} C  "
                  f"smoke {smoke:.2f} V  heater={'ON ' if heater_on else 'OFF'}"
                  f"  alarm={'YES' if want_buzz else 'no '}  <- {why}")
            last_reason = why

        time.sleep(CYCLE_S)
finally:
    # The physical world does not reset when the process exits.
    room.set_buzzer(False)
    room.set_heater(False)
    publish_heater("off")
    if _mqtt:
        _mqtt.loop_stop()
    print("\nstopped — heater off, alarm off")
