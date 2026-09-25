"""
Level 4 sensor — same as level 3's sensor, but ALSO publishes each
reading to MQTT for the data pipeline.

Two writes per reading:
  A) BuildSim REST  → live UI (the present)
  B) MQTT broker    → consumer → DuckDB (the past, for the agent + training)

Run:
    docker compose up -d           # start Mosquitto
    python level4_consumer.py      # one terminal
    python level4_sensor.py        # another terminal

The actuator and simulator come from level3 — keep level3_sim_loop.py
running too, OR copy its sim+actuator threads into your own combined
file. For simplicity, this script also runs its own simulator & actuator
threads inline so you can run just this + the consumer.
"""
import requests, threading, time, json, signal, sys
import paho.mqtt.client as mqtt

BASE = "http://localhost:9090"
MQTT_HOST = "localhost"
MQTT_PORT = 1883

SENSOR_EQ_ID  = "level4-temp-A109"
SENSOR_VAL_ID = "level4-temp-A109-val"
ACT_EQ_ID     = "level4-heater-A109"
ACT_STATE_ID  = "level4-heater-A109-state"
ROOM, LEVEL   = "A109", "level0"
TOPIC         = f"sensors/{LEVEL}/{ROOM}/temperature"

# ---------------- MQTT client ----------------
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                          client_id="level4-sensor")
mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
mqtt_client.loop_start()

# ---------------- Simulator ----------------
class Simulator:
    def __init__(self):
        self.temp = 22.0
        self.heater_on = False
        self.outdoor = 5.0
    def tick(self, dt):
        # Same tuned coefficients as level3 — see level3_sim_loop.py for the
        # full explanation. Heater equilibrium = 65 °C, off equilibrium = outdoor.
        gain = 3.0 if self.heater_on else 0.0
        loss = (self.temp - self.outdoor) * 0.05
        self.temp += (gain - loss) * dt

sim = Simulator()
running = True

# ---------------- Sensor (publishes BuildSim + MQTT) ----------------
def sensor():
    requests.post(f"{BASE}/api/equipment", json={
        "id": SENSOR_EQ_ID, "name": "Level4 Temp",
        "type": "temperature_sensor", "category": "monitoring",
        "level": LEVEL, "room": ROOM, "status": "running"})
    requests.post(f"{BASE}/api/equipment/{SENSOR_EQ_ID}/sensors", json={
        "id": SENSOR_VAL_ID, "name": "Temperature",
        "type": "temperature", "data_type": "text",
        "unit": "C", "value": f"{sim.temp:.2f}"})
    requests.post(f"{BASE}/api/equipment/notify")
    print(f"[sensor] registered, publishing both REST + MQTT topic={TOPIC}")

    while running:
        v = sim.temp + (((-1)**int(time.time())) * 0.05)
        ts = time.time()

        # A) BuildSim — live UI
        try:
            requests.put(f"{BASE}/api/sensors/{SENSOR_VAL_ID}/value",
                         json={"data_type": "text",
                               "value": f"{v:.2f}"},
                         timeout=2.0)
        except requests.RequestException as e:
            print(f"[sensor] BuildSim publish failed: {e}")

        # B) MQTT — pipeline
        try:
            payload = json.dumps({
                "ts": ts,
                "sensor_id": SENSOR_VAL_ID,
                "room": ROOM, "level": LEVEL,
                "type": "temperature", "unit": "C",
                "value": v,
            })
            mqtt_client.publish(TOPIC, payload, qos=1)
        except Exception as e:
            print(f"[sensor] MQTT publish failed: {e}")

        time.sleep(1.0)

# ---------------- Actuator (poll BuildSim, mirror to sim) ----------------
def actuator():
    requests.post(f"{BASE}/api/equipment", json={
        "id": ACT_EQ_ID, "name": "Level4 Heater",
        "type": "radiator", "category": "hvac",
        "level": LEVEL, "room": ROOM, "status": "running"})
    requests.post(f"{BASE}/api/equipment/{ACT_EQ_ID}/actuators", json={
        "id": ACT_STATE_ID, "name": "On/Off",
        "type": "on_off", "state": "off"})
    requests.post(f"{BASE}/api/equipment/notify")
    print(f"[actuator] registered {ACT_STATE_ID}")

    last_seen = "off"
    while running:
        try:
            r = requests.get(f"{BASE}/api/equipment/{ACT_EQ_ID}",
                             timeout=2.0).json()
            state = r["actuators"][0]["state"]
            if state != last_seen:
                sim.heater_on = (state == "on")
                print(f"[actuator] state -> {state}")
                last_seen = state
        except Exception as e:
            print(f"[actuator] poll failed: {e}")
        time.sleep(0.5)

def ticker():
    while running:
        sim.tick(0.5)
        time.sleep(0.5)

def cleanup(*_):
    global running
    running = False
    time.sleep(0.5)
    for eq in (SENSOR_EQ_ID, ACT_EQ_ID):
        try: requests.delete(f"{BASE}/api/equipment/{eq}", timeout=2.0)
        except Exception: pass
    try: requests.post(f"{BASE}/api/equipment/notify", timeout=2.0)
    except Exception: pass
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    threading.Thread(target=sensor,   daemon=True).start()
    threading.Thread(target=actuator, daemon=True).start()
    threading.Thread(target=ticker,   daemon=True).start()

    print()
    print(f"Sensor publishing on MQTT topic: {TOPIC}")
    print(f"Toggle heater:")
    print(f'  curl -X PUT {BASE}/api/actuators/{ACT_STATE_ID}/state '
          f'-H "Content-Type: application/json" -d \'{{"state":"on"}}\'')
    print()
    try:
        while True:
            time.sleep(60)
            print(f"[status] sim.temp={sim.temp:.2f}  heater={sim.heater_on}")
    except KeyboardInterrupt:
        cleanup()
