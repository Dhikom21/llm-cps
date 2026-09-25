"""
level4_multi_room_sensor.py -- three SIMULATED rooms (A109, A110, A111), each with its
own physics, temperature sensor, and heater. Same idea as level4_sensor.py,
just generalized to several rooms (a new file; nothing existing is touched).

Each room:
  * has real thermal physics (heater in, walls leak out) -- see level3's tick()
  * publishes its temperature to BuildSim (REST) + MQTT (for the DB pipeline)
  * has a heater you can toggle; the actuator applies it to that room's sim

They start at different temperatures so the heatmap is colourful immediately,
and because each has a real heater, turning one on visibly warms that room.

Prerequisites:
    BuildSim + browser tab at http://localhost:9090
    docker compose up -d            # TimescaleDB (+ Mosquitto)
    python level4_consumer.py       # store readings
    python heatmap_buildsim.py      # colour the rooms

Run:
    python level4_multi_room_sensor.py

Toggle a room's heater (printed at startup), e.g.:
    curl -X PUT http://localhost:9090/api/actuators/sim-A110-heater-state/state \
         -H "Content-Type: application/json" -d '{"state":"on"}'
"""
import json
import signal
import sys
import threading
import time

import requests
import paho.mqtt.client as mqtt

BASE      = "http://localhost:9090"
MQTT_HOST = "localhost"
MQTT_PORT = 1883
LEVEL     = "level0"

# room -> starting temperature (spread out so colours differ from the start)
ROOMS = {
    "A109": 22.0,
    "A110": 18.0,
    "A111": 26.0,
}

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="multi-room-sim")
mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
mqtt_client.loop_start()

running = True


class Room:
    def __init__(self, name, start_temp):
        self.name       = name
        self.temp       = start_temp
        self.heater_on  = False
        self.outdoor    = 5.0
        # BuildSim ids
        self.temp_eq    = f"sim-{name}-temp"
        self.temp_val   = f"sim-{name}-temp-val"
        self.heat_eq    = f"sim-{name}-heater"
        self.heat_state = f"sim-{name}-heater-state"

    def tick(self, dt):
        gain = 3.0 if self.heater_on else 0.0
        loss = (self.temp - self.outdoor) * 0.05
        self.temp += (gain - loss) * dt


rooms = {name: Room(name, t) for name, t in ROOMS.items()}


def register(room: Room):
    # temperature sensor
    requests.post(f"{BASE}/api/equipment", json={
        "id": room.temp_eq, "name": f"{room.name} Temp",
        "type": "temperature_sensor", "category": "monitoring",
        "level": LEVEL, "room": room.name, "status": "running"})
    requests.post(f"{BASE}/api/equipment/{room.temp_eq}/sensors", json={
        "id": room.temp_val, "name": "Temperature", "type": "temperature",
        "data_type": "text", "unit": "C", "value": f"{room.temp:.2f}"})
    # heater actuator
    requests.post(f"{BASE}/api/equipment", json={
        "id": room.heat_eq, "name": f"{room.name} Heater",
        "type": "radiator", "category": "hvac",
        "level": LEVEL, "room": room.name, "status": "running"})
    requests.post(f"{BASE}/api/equipment/{room.heat_eq}/actuators", json={
        "id": room.heat_state, "name": "On/Off", "type": "on_off", "state": "off"})


def sensor(room: Room):
    while running:
        v = room.temp
        # BuildSim (live UI)
        try:
            requests.put(f"{BASE}/api/sensors/{room.temp_val}/value",
                         json={"data_type": "text", "value": f"{v:.2f}"}, timeout=2.0)
        except requests.RequestException:
            pass
        # MQTT (pipeline -> DB)
        try:
            mqtt_client.publish(
                f"sensors/{LEVEL}/{room.name}/temperature",
                json.dumps({"ts": time.time(), "sensor_id": room.temp_val,
                            "room": room.name, "level": LEVEL,
                            "type": "temperature", "unit": "C", "value": v}),
                qos=1)
        except Exception:
            pass
        time.sleep(1.0)


def actuator(room: Room):
    last = "off"
    while running:
        try:
            r = requests.get(f"{BASE}/api/equipment/{room.heat_eq}", timeout=2.0).json()
            state = r["actuators"][0]["state"]
            if state != last:
                room.heater_on = (state == "on")
                print(f"[{room.name}] heater -> {state}")
                last = state
        except Exception:
            pass
        time.sleep(0.5)


def ticker():
    while running:
        for room in rooms.values():
            room.tick(0.5)
        time.sleep(0.5)


def cleanup(*_):
    global running
    running = False
    print("\ncleaning up ...")
    time.sleep(0.5)
    for room in rooms.values():
        for eq in (room.temp_eq, room.heat_eq):
            try: requests.delete(f"{BASE}/api/equipment/{eq}", timeout=2.0)
            except Exception: pass
    try: requests.post(f"{BASE}/api/equipment/notify", timeout=2.0)
    except Exception: pass
    mqtt_client.loop_stop(); mqtt_client.disconnect()
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

# register everything, then start threads
for room in rooms.values():
    register(room)
requests.post(f"{BASE}/api/equipment/notify")

for room in rooms.values():
    threading.Thread(target=sensor,   args=(room,), daemon=True).start()
    threading.Thread(target=actuator, args=(room,), daemon=True).start()
threading.Thread(target=ticker, daemon=True).start()

print(f"multi-room sim running: {list(ROOMS)}. Ctrl-C to stop.\nToggle a heater:")
for room in rooms.values():
    print(f"  curl -X PUT {BASE}/api/actuators/{room.heat_state}/state "
          f"-H 'Content-Type: application/json' -d '{{\"state\":\"on\"}}'")
print()

try:
    while True:
        time.sleep(20)
        print("[status] " + "  ".join(f"{r.name}={r.temp:.1f}({'ON' if r.heater_on else 'off'})"
                                       for r in rooms.values()))
except KeyboardInterrupt:
    cleanup()
