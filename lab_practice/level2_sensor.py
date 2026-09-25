"""
Level 2 — One sensor process.

Smallest possible 'sensor process': registers itself with BuildSim, then
publishes a value once per second. The value is just a random walk —
there is no physical simulator yet (that's level 3).

Run BuildSim first (`make run` in ../buildingsim), open Chrome at
http://localhost:9090, then:

    python level2_sensor.py

In the browser, click on room A109 — you should see the temperature
update every second.
"""
import requests
import time
import random
import signal
import sys

BASE = "http://localhost:9090"
EQUIP_ID  = "level2-temp-A109"
SENSOR_ID = "level2-temp-A109-val"
ROOM      = "A109"
LEVEL     = "level0"

def register():
    """Idempotent registration — re-running this file should not duplicate."""
    eq = {
        "id": EQUIP_ID,
        "name": "Level2 Temperature",
        "type": "temperature_sensor",
        "category": "monitoring",
        "level": LEVEL,
        "room": ROOM,
        "status": "running",
    }
    r = requests.post(f"{BASE}/api/equipment", json=eq)
    if r.status_code not in (200, 201, 409):
        print(f"register equipment: HTTP {r.status_code} {r.text}")
    sensor = {
        "id": SENSOR_ID,
        "name": "Temperature",
        "type": "temperature",
        "data_type": "text",
        "unit": "C",
        "value": "21.0",
    }
    r = requests.post(f"{BASE}/api/equipment/{EQUIP_ID}/sensors", json=sensor)
    if r.status_code not in (200, 201, 409):
        print(f"register sensor: HTTP {r.status_code} {r.text}")
    requests.post(f"{BASE}/api/equipment/notify")
    print(f"registered {EQUIP_ID} / {SENSOR_ID} in {ROOM}")

def publish_loop():
    t = 21.0
    while True:
        # 'physics': random walk, no simulator yet
        t += random.uniform(-0.3, 0.3)
        try:
            r = requests.put(
                f"{BASE}/api/sensors/{SENSOR_ID}/value",
                json={"data_type": "text", "value": f"{t:.2f}"},
                timeout=2.0,
            )
            if r.status_code != 200:
                print(f"publish: HTTP {r.status_code}")
            else:
                print(f"  -> {t:.2f}")
        except requests.RequestException as e:
            print(f"publish failed (BuildSim down?): {e}")
        time.sleep(1.0)

def cleanup_handler(signum, frame):
    print("\nCleaning up demo equipment ...")
    try:
        requests.delete(f"{BASE}/api/equipment/{EQUIP_ID}", timeout=2.0)
        requests.post(f"{BASE}/api/equipment/notify", timeout=2.0)
    except Exception:
        pass
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup_handler)
    register()
    publish_loop()
