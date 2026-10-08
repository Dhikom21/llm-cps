"""
pi_sensor.py — the sensor process. Reads, publishes, and nothing else.

A DS18B20 is a chip with three legs: no processor, no network, no ability to
publish anything. So something has to read the bus and publish on its behalf,
and in a clean design that something is a process whose ONLY job is this. It
makes no decisions and touches no actuator.

    1-Wire bus  ->  pi_sensor.py  ->  MQTT sensors/{level}/{room}/reading
                                  ->  BuildSim (so the floor plan is live)

--------------------------------------------------------------------------
Why this is its own process
--------------------------------------------------------------------------
Previously the agent did this itself, which conflated four roles — sensing,
storing, deciding and acting — in one program. One unreadable sensor then took
the whole controller down, because the thing that failed and the thing that
controls the room were the same process.

Now a sensor fault is contained: this process logs it, publishes nothing for
that room, and the agent carries on with whatever it last knew. Restarting the
sensor process does not interrupt control.

It also claims no GPIO output pins, which is what allows pi_actuator.py to own
them exclusively.

--------------------------------------------------------------------------
Run
--------------------------------------------------------------------------
    export BUILDSIM_URL=http://localhost:9090
    python3 pi_sensor.py

    HW=fake python3 pi_sensor.py        # no Pi attached
"""
import os
import signal
import time

import bus
import rooms

SIMULATED = os.environ.get("HW", "real") == "fake"

if SIMULATED:
    import fake_hardware as hw
else:
    import real_hardware as hw

PERIOD_S = float(os.environ.get("SENSOR_PERIOD_S", "5.0"))

running = True


def stop(*_):
    global running
    running = False


def main():
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    sensors = hw.get_sensors()
    link = bus.Bus("pi-sensor").connect()
    print(f"sensor process: {rooms.names()} every {PERIOD_S}s -> "
          f"{bus.MQTT_HOST}:{bus.MQTT_PORT}")
    print("(the twin is fed by pi_twin_bridge.py, not from here)")

    # ------------------------------------------------------------------
    # Closing the loop when the hardware is simulated
    # ------------------------------------------------------------------
    # With real hardware the ROOM is the plant: the relay heats the air, the
    # air warms the DS18B20, and the feedback path is physical. Nothing in
    # software has to connect the two.
    #
    # With HW=fake the plant is the thermal model inside this process — and
    # it lives in a DIFFERENT process from the one holding the actuators. Its
    # _Room.heater would stay False forever, so the simulated temperature
    # would drift to OUTDOOR no matter what the agent commanded, and the
    # control loop would be silently open. The run would look like a failed
    # controller when it is really a disconnected simulation.
    #
    # So in simulated mode only, this process listens to the retained
    # actuator state topics and applies them to its own model. That is the
    # software stand-in for the physical path from relay to air to sensor.
    # In real mode this subscription is not just unnecessary but wrong: the
    # temperature must come from the sensor, not from what we commanded.
    if SIMULATED:
        def on_state(topic, payload):
            room = payload.get("room")
            actuator = payload.get("actuator")
            if room not in rooms.ROOMS:
                return
            on = payload.get("state") == "on"
            if actuator == "heater":
                sensors.set_heater(room, on)
            elif actuator == "buzzer":
                sensors.set_buzzer(room, on)

        link.subscribe(bus.ALL_STATES, on_state)
        print("HW=fake: thermal model driven by actuators/# "
              "(simulated plant, real control path)")

    last = {}
    try:
        while running:
            now = time.time()
            for room in rooms.names():
                temp  = sensors.read_temperature(room)
                smoke = sensors.read_smoke(room)

                # A failed temperature is published as null rather than
                # skipped. "I looked and could not tell" is information; a
                # gap in the stream is indistinguishable from the process
                # having died, which is a very different thing.
                link.publish(bus.reading_topic(rooms.level(room), room), {
                    "room": room,
                    "level": rooms.level(room),
                    "temp_c": temp,
                    "smoke_v": smoke,
                    "ts": now,
                })

                line = f"{room} {temp} C, smoke {smoke} V"
                if line != last.get(room):
                    print(f"{time.strftime('%H:%M:%S')}  {line}")
                    last[room] = line

            time.sleep(PERIOD_S)
    finally:
        link.close()
        sensors.close()
        print("\nsensor process stopped")


if __name__ == "__main__":
    main()
