"""
replay_kaggle.py -- stream the Kaggle Environmental Sensor Telemetry dataset
into the SAME pipeline as level4_sensor.py (MQTT -> level4_consumer -> TimescaleDB).

What it does:
  * reads iot_telemetry_data.csv
  * keeps ONE device and treats it as room A109
  * for each row, publishes temperature, CO, and smoke as separate MQTT
    messages (one per sensor type), re-stamped to NOW so the agents'
    "last N seconds" queries see them as a live feed
  * runs ~1 reading/sec

Prerequisites (same as level 4):
    docker compose up -d          # Mosquitto + TimescaleDB
    python level4_consumer.py     # in another terminal (writes to the DB)

Run:
    python replay_kaggle.py

Then the Level 5/6/7 agents can run against real data unchanged.

NOTE: this is a DATA REPLAY, not a closed loop. The temperature comes from a
recording, so an agent turning the heater on/off will NOT change these values
(unlike the simulator in level3). That is expected and fine -- the goal here is
to feed the agent REAL sensor streams (incl. real CO/smoke spikes) to observe
how it decides.
"""
import csv
import json
import signal
import sys
import time

import paho.mqtt.client as mqtt

CSV_FILE  = "iot_telemetry_data.csv"
LEVEL     = "level0"
PERIOD_S  = 1.0                    # seconds between rows

MQTT_HOST = "localhost"
MQTT_PORT = 1883

# Map each Kaggle device -> a BuildSim room
DEVICE_ROOMS = {
    "b8:27:eb:bf:9d:51": "A109",   # warm/dry   ~22.7 C
    "00:0f:00:70:91:0a": "A110",   # cool/humid ~19.7 C
    "1c:bf:ce:15:ec:4d": "A111",   # warmest    ~27   C
}

# Which CSV columns to publish, and their reading type + unit
CHANNELS = {
    "temp":  ("temperature", "C"),
    "co":    ("co",          "ratio"),
    "smoke": ("smoke",       "ratio"),
}

# ---- MQTT connection ----
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="kaggle-replay")
client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
client.loop_start()

sent = 0

def cleanup(*_):
    print(f"\nstopping replay. published {sent} messages.")
    client.loop_stop()
    client.disconnect()
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

print(f"replaying {len(DEVICE_ROOMS)} devices -> {list(DEVICE_ROOMS.values())}: "
      f"temp + co + smoke, ~1 row/{PERIOD_S:.0f}s. Ctrl-C to stop.")

with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    rows = 0
    for row in reader:
        room = DEVICE_ROOMS.get(row["device"])
        if room is None:
            continue                      # a device we're not using
        try:
            temp = float(row["temp"])
        except (KeyError, ValueError):
            continue
        if temp == 0.0:
            continue                      # skip sensor dropouts

        now = time.time()                 # RE-STAMP to now (live feed)
        for col, (rtype, unit) in CHANNELS.items():
            try:
                value = float(row[col])
            except (KeyError, ValueError):
                continue
            payload = json.dumps({
                "ts": now,
                "sensor_id": f"kaggle-{room}-{rtype}",
                "room": room,
                "level": LEVEL,
                "type": rtype,
                "unit": unit,
                "value": value,
            })
            topic = f"sensors/{LEVEL}/{room}/{rtype}"
            client.publish(topic, payload, qos=1)
            sent += 1

        rows += 1
        if rows % 10 == 0:
            print(f"  {room}: temp={row['temp']}  co={row['co']}  smoke={row['smoke']}")

        time.sleep(PERIOD_S)

print("reached end of CSV.")
cleanup()
