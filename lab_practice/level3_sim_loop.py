"""
Level 3 — Closed loop in one file.

Three threads sharing state, simulating what should be three separate
Unix processes in production:
  1. Simulator       — owns the 'true' physics of room A109
  2. Sensor process  — reads simulator, publishes to BuildSim
  3. Actuator process— polls BuildSim's actuator state, applies it to sim

Run BuildSim first, then:

    python level3_sim_loop.py

In another terminal, fire the heater:

    curl -X PUT http://localhost:9090/api/actuators/level3-heater-A109-state/state \
         -H "Content-Type: application/json" -d '{"state":"on"}'

Watch the temperature in the browser climb. Turn it off:

    curl -X PUT http://localhost:9090/api/actuators/level3-heater-A109-state/state \
         -H "Content-Type: application/json" -d '{"state":"off"}'

This is THE demo of the closed loop. Show it to students in week 1.
"""
import requests, threading, time, signal, sys

BASE = "http://localhost:9090"

# IDs reused across runs (idempotent registration)
SENSOR_EQ_ID    = "level3-temp-A109"
SENSOR_VAL_ID   = "level3-temp-A109-val"
ACT_EQ_ID       = "level3-heater-A109"
ACT_STATE_ID    = "level3-heater-A109-state"
ROOM, LEVEL     = "A109", "level0"

# ---------------- Physical simulator (truth) ----------------
class Simulator:
    """Tutorial physics (tutorials/buildsim.md Part 3): each tick the room
    temperature moves a fixed fraction of the way toward a target.

        temp += 0.2 * (target - temp)      # close 20% of the gap every 2 s

    On/off heater kept: heater ON -> warm_target, heater OFF -> outdoor. The
    temperature eases up to the target and settles there (no 65 C runaway).
    'The simulator does not need to be physically perfect.'"""
    APPROACH_PER_2S = 0.2      # tutorial value: 20% of the gap closed per 2 s

    def __init__(self):
        self.temp = 22.0
        self.heater_on = False
        self.outdoor = 5.0        # target when heater is OFF (cools toward this)
        self.warm_target = 25.0   # target when heater is ON  (settles here)

    def tick(self, dt):
        # The heater picks the target; the room moves a fraction of the way to it.
        target = self.warm_target if self.heater_on else self.outdoor
        # Scale the tutorial's per-2-second fraction to this tick's dt, so the
        # curve is the same no matter how often the ticker calls tick().
        frac = self.APPROACH_PER_2S * (dt / 2.0)
        self.temp += frac * (target - self.temp)

sim = Simulator()
running = True

# ---------------- Sensor process ----------------
def sensor():
    # equipment
    requests.post(f"{BASE}/api/equipment", json={
        "id": SENSOR_EQ_ID, "name": "Level3 Temp",
        "type": "temperature_sensor", "category": "monitoring",
        "level": LEVEL, "room": ROOM, "status": "running"})
    requests.post(f"{BASE}/api/equipment/{SENSOR_EQ_ID}/sensors", json={
        "id": SENSOR_VAL_ID, "name": "Temperature",
        "type": "temperature", "data_type": "text",
        "unit": "C", "value": f"{sim.temp:.2f}"})
    requests.post(f"{BASE}/api/equipment/notify")
    print(f"[sensor] registered {SENSOR_VAL_ID}")

    while running:
        # tiny noise to mimic a real sensor
        v = sim.temp + (((-1)**int(time.time())) * 0.05)
        try:
            requests.put(f"{BASE}/api/sensors/{SENSOR_VAL_ID}/value",
                         json={"data_type":"text", "value": f"{v:.2f}"},
                         timeout=2.0)
        except requests.RequestException as e:
            print(f"[sensor] publish failed: {e}")
        time.sleep(1.0)

# ---------------- Actuator process ----------------
def actuator():
    requests.post(f"{BASE}/api/equipment", json={
        "id": ACT_EQ_ID, "name": "Level3 Heater",
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
                print(f"[actuator] BuildSim says {state!r}, "
                      f"sim.heater_on={sim.heater_on}")
                last_seen = state
        except Exception as e:
            print(f"[actuator] poll failed: {e}")
        time.sleep(0.5)

# ---------------- Sim ticker ----------------
def ticker():
    while running:
        sim.tick(0.5)
        time.sleep(0.5)

# ---------------- Cleanup ----------------
def cleanup_and_exit(*_):
    global running
    running = False
    print("\nCleaning up ...")
    time.sleep(0.5)
    for eq in (SENSOR_EQ_ID, ACT_EQ_ID):
        try:
            requests.delete(f"{BASE}/api/equipment/{eq}", timeout=2.0)
        except Exception:
            pass
    try:
        requests.post(f"{BASE}/api/equipment/notify", timeout=2.0)
    except Exception:
        pass
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup_and_exit)

    threading.Thread(target=sensor,   daemon=True).start()
    threading.Thread(target=actuator, daemon=True).start()
    threading.Thread(target=ticker,   daemon=True).start()

    print()
    print("Closed-loop demo running. From another terminal:")
    print()
    print(f'  curl -X PUT {BASE}/api/actuators/{ACT_STATE_ID}/state '
          f'-H "Content-Type: application/json" -d \'{{"state":"on"}}\'')
    print()
    print("Then watch the temperature in A109 in the 3D viewer.")
    print("Press Ctrl-C to stop and clean up.")
    print()

    try:
        while True:
            time.sleep(5)
            print(f"[status] sim.temp = {sim.temp:.2f}, "
                  f"heater = {sim.heater_on}")
    except KeyboardInterrupt:
        cleanup_and_exit()
