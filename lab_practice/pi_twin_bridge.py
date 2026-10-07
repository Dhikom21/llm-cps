"""
pi_twin_bridge.py — translates the message bus into BuildSim's REST API.

BuildSim does not speak MQTT. It is a REST server with a WebSocket for its
viewer, and we do not modify it — it is the environment, not our code. So
something has to carry facts from the bus to its HTTP endpoints.

That something is this, and only this:

    MQTT sensors/#    ──►  PUT /api/sensors/{id}/value
    MQTT actuators/#  ──►  PUT /api/actuators/{id}/state

--------------------------------------------------------------------------
Why a separate process rather than a few extra lines elsewhere
--------------------------------------------------------------------------
pi_sensor.py used to publish each reading twice: once on MQTT for our
pipeline, once over HTTP for the twin. That worked, but it meant a process
whose job is "read the 1-Wire bus" also had to know BuildSim's URL, its
endpoint names and its id conventions. The same was true of the actuator
process.

With the bridge, the twin becomes just another subscriber — exactly like the
database. Nothing publishes to it directly; it consumes the same stream
everyone else does. Three consequences:

  - pi_sensor.py and pi_actuator.py no longer import `twin` at all;
  - if BuildSim is down or slow, only this process is affected, and readings
    keep flowing to the database and the agent;
  - every arrow leaving the broker has the same shape, so the architecture
    diagram stops needing two special cases.

--------------------------------------------------------------------------
What it does NOT do
--------------------------------------------------------------------------
It is one-directional: bus to twin. Smoke still travels the other way —
inject_fire.py writes it into BuildSim and pi_sensor.py reads it back out over
HTTP — because the simulated fire lives in the twin and the twin is its
origin. A bridge cannot change which end of a value is authoritative.

It also owns equipment registration, since it is now the only process that
talks to BuildSim about our devices.

--------------------------------------------------------------------------
Run
--------------------------------------------------------------------------
    export BUILDSIM_URL=http://localhost:9090
    python3 pi_twin_bridge.py
"""
import signal
import time

import bus
import rooms
import twin

running = True
stats = {"readings": 0, "states": 0, "failed": 0}


def stop(*_):
    global running
    running = False


def on_reading(topic, payload):
    """A sensor reading arrived. Show it on the floor plan."""
    room = payload.get("room")
    temp = payload.get("temp_c")
    if not room or temp is None:
        # A null temperature means the sensor could not be read. Publishing
        # nothing is right: leaving the last good value on screen is less
        # misleading than showing a zero, and the agent is told separately.
        return
    twin.publish_temp(room, temp)
    stats["readings"] += 1


def on_state(topic, payload):
    """An actuator changed. Mirror it so the twin matches the hardware."""
    room = payload.get("room")
    actuator = payload.get("actuator")
    state = payload.get("state")
    if not (room and actuator and state):
        return
    twin.publish_actuator(room, actuator, state)
    stats["states"] += 1
    print(f"{time.strftime('%H:%M:%S')}  {room}/{actuator} -> {state}"
          f"{'' if payload.get('physical') else '  (virtual)'}")


def main():
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    # This process is now the only one that registers equipment, because it is
    # the only one that talks to BuildSim about our devices.
    twin.register_all()

    link = bus.Bus("pi-twin-bridge").connect()
    link.subscribe(bus.ALL_READINGS, on_reading)
    link.subscribe(bus.ALL_STATES, on_state)

    print(f"twin bridge: {bus.ALL_READINGS} and {bus.ALL_STATES} "
          f"-> {twin.BUILDSIM}")
    print(f"rooms: {', '.join(rooms.names())}")

    try:
        n = 0
        while running:
            time.sleep(2.0)
            n += 1
            if n % 30 == 0:                    # every ~60 s
                print(f"{time.strftime('%H:%M:%S')}  "
                      f"{stats['readings']} readings, "
                      f"{stats['states']} state changes forwarded")
    finally:
        link.close()
        print(f"\nbridge stopped — {stats['readings']} readings, "
              f"{stats['states']} state changes forwarded")


if __name__ == "__main__":
    main()
