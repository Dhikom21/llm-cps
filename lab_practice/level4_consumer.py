"""
Level 4 consumer (TimescaleDB version).

Subscribes to all sensor topics on MQTT and inserts every reading into
the TimescaleDB `readings` hypertable. Multiple readers (the agent, the
query script) can run concurrently against TimescaleDB while this is
inserting — that's why we left DuckDB behind.

Pre-requisites:
    docker compose up -d                # mosquitto + timescaledb
    pip install -r requirements.txt     # paho-mqtt + psycopg2-binary

Run:
    python level4_consumer.py

Inspect any time:
    python query_readings.py
"""
import json
import signal
import sys
import time

import paho.mqtt.client as mqtt
import psycopg2

import db

MQTT_HOST = "localhost"
MQTT_PORT = 1883
TOPIC_FILTER = "sensors/#"

INSERT_SQL = """
    INSERT INTO readings (ts, sensor_id, room, floor, type, unit, value)
    VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s)
"""

# Wait for TimescaleDB to be ready (it can take ~5 s on cold start)
def wait_for_db(timeout_s: int = 60):
    start = time.time()
    while True:
        try:
            db.ensure_schema()
            print("connected to TimescaleDB; schema ready")
            return
        except psycopg2.OperationalError as e:
            if time.time() - start > timeout_s:
                print(f"giving up — TimescaleDB not reachable: {e}")
                sys.exit(1)
            print(f"waiting for TimescaleDB ... ({e})")
            time.sleep(2)

wait_for_db()

# Long-lived connection for high-frequency inserts
conn = db.connect(autocommit=True)
cur  = conn.cursor()

count = 0

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"connected to MQTT (rc={reason_code}); subscribing {TOPIC_FILTER}")
    client.subscribe(TOPIC_FILTER, qos=1)

def on_message(client, userdata, msg):
    global count
    try:
        r = json.loads(msg.payload)
    except Exception as e:
        print(f"bad payload on {msg.topic}: {e}")
        return

    try:
        cur.execute(INSERT_SQL, [
            r["ts"],
            r["sensor_id"],
            r.get("room"),
            r.get("level"),     # JSON field name → SQL column "floor"
            r.get("type"),
            r.get("unit"),
            r["value"],
        ])
        count += 1
        if count % 10 == 0:
            print(f"  stored {count} readings, last: "
                  f"{r['sensor_id']}={r['value']}")
    except Exception as e:
        # Don't crash the consumer on a bad row; reconnect if the connection died.
        print(f"insert failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass

def cleanup(*_):
    print(f"\nshutting down. {count} readings stored in TimescaleDB.")
    try: cur.close()
    except Exception: pass
    try: conn.close()
    except Exception: pass
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                     client_id="level4-consumer")
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
client.loop_forever()
