"""
pi_consumer.py — the data pipeline. Subscribes, stores, and nothing else.

    MQTT sensors/#  ->  pi_consumer.py  ->  SQLite pi_readings.sqlite

It never reads a sensor, never makes a decision, never touches a pin. It
listens for readings and writes them down.

--------------------------------------------------------------------------
Why the writer is separate from the reader
--------------------------------------------------------------------------
The sensor process does not know a database exists. That is the whole point of
putting a broker between them:

  - storage can change — SQLite to Postgres, or two stores at once — without
    the sensor process changing at all;
  - a slow or locked database backs up here, not in the loop that controls the
    room;
  - a second consumer (a dashboard, an analyser) costs nothing to add, because
    the publisher never learns it exists.

--------------------------------------------------------------------------
What this does NOT guarantee
--------------------------------------------------------------------------
Durability. QoS 1 means the broker will redeliver until this process
acknowledges, so a brief restart here loses nothing — but Mosquitto's default
configuration keeps no persistent queue across its own restart. If the broker
is restarted, in-flight readings are gone. That is acceptable for a lab rig
and would not be for a building.

--------------------------------------------------------------------------
Run
--------------------------------------------------------------------------
    python3 pi_consumer.py
"""
import signal
import time

import bus
import history

running = True
stats = {"stored": 0, "ignored": 0, "first": None, "last": None}


def stop(*_):
    global running
    running = False


def on_reading(topic, payload):
    """One reading has arrived. Write it down."""
    room = payload.get("room")
    if not room:
        stats["ignored"] += 1
        return

    history.record(room,
                   payload.get("temp_c"),
                   payload.get("smoke_v"),
                   payload.get("ts") or time.time())

    stats["stored"] += 1
    stats["last"] = room
    if stats["first"] is None:
        stats["first"] = time.strftime("%H:%M:%S")
        print(f"{stats['first']}  first reading stored ({room})")


def main():
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    # The consumer writes; it must not also publish, or it would feed itself.
    history.PUBLISH_TO_MQTT = False

    link = bus.Bus("pi-consumer").connect()
    link.subscribe(bus.ALL_READINGS, on_reading)
    print(f"consumer process: {bus.ALL_READINGS} -> {history.DB_PATH}")

    try:
        n = 0
        while running:
            time.sleep(2.0)
            n += 1
            if n % 15 == 0:                    # every ~30 s
                print(f"{time.strftime('%H:%M:%S')}  "
                      f"{stats['stored']} readings stored")
            if n % 150 == 0:                   # every ~5 min
                history.prune()
    finally:
        link.close()
        print(f"\nconsumer stopped — {stats['stored']} readings stored, "
              f"{stats['ignored']} ignored")


if __name__ == "__main__":
    main()
