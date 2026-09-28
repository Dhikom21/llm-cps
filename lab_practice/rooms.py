"""
rooms.py — which rooms exist, and what is real in each of them.

Every other module reads this. Room names, sensor IDs and which actuators are
physical all live here, so adding a third room is a change to this file and
nowhere else.

--------------------------------------------------------------------------
Why sensors are named, not numbered
--------------------------------------------------------------------------
The old code used `sensors[0]`, which meant "whichever DS18B20 the kernel
happened to list first". That is a poor thing to hang a building on: plug in a
third sensor and the rooms silently swap. Here each room names the exact 64-bit
ID of its sensor, so the mapping is stated rather than inferred.

Find your IDs with:
    ls /sys/bus/w1/devices/
or
    python3 -c "from w1thermsensor import W1ThermSensor as W; \
                print([s.id for s in W.get_available_sensors()])"

Then pin them, so a rewire cannot quietly reassign a room:
    export SENSOR_A109=000011a8870a
    export SENSOR_A108=000011a8fa32

--------------------------------------------------------------------------
On the asymmetry
--------------------------------------------------------------------------
A109 has a real relay and a real buzzer. A108 has neither, so its actuators are
virtual — state is tracked and shown in the twin, but nothing moves.

This is deliberate, not a shortage. It means a command sent to the wrong room
has different consequences in each direction: A109 -> A108 is a silent error
visible only in the log, while A108 -> A109 makes a relay click in a room that
is not on fire. Keeping that asymmetry gives the misattribution experiments
something to measure.
"""
import os

ROOMS = {
    "A109": {
        "level": "level0",
        "sensor_id": os.environ.get("SENSOR_A109", "000011a8870a"),
        "heater": "real",       # the Grove relay on GPIO17
        "buzzer": "real",       # the piezo on GPIO27
    },
    "A108": {
        "level": "level0",
        "sensor_id": os.environ.get("SENSOR_A108", "000011a8fa32"),
        "heater": "virtual",    # exists in the twin only
        "buzzer": "virtual",
    },
}

ACTUATORS = ("heater", "buzzer")

# Control targets. Per-room so rooms can differ later (a server room wants a
# different band from an office) without touching any agent code.
DEFAULT_BAND = (
    float(os.environ.get("BAND_LO", "22.0")),
    float(os.environ.get("BAND_HI", "24.0")),
)
SMOKE_THRESHOLD = float(os.environ.get("SMOKE_THRESHOLD", "1.0"))


def names():
    """Room names, in a stable order — so logs and prompts stay comparable."""
    return sorted(ROOMS)


def level(room):
    return ROOMS[room]["level"]


def band(room):
    r = ROOMS[room]
    return r.get("band", DEFAULT_BAND)


def is_real(room, actuator):
    return ROOMS.get(room, {}).get(actuator) == "real"


def actuator_id(room, actuator):
    """The canonical name an agent must use: 'A109/heater'.

    Room and actuator in one string, because the pair is what has to be right.
    An agent that names the actuator correctly but the room wrongly has still
    made a mistake, and this makes that mistake expressible and checkable.
    """
    return f"{room}/{actuator}"


def parse_actuator_id(value):
    """'A109/heater' -> ('A109', 'heater'). Returns (None, None) if malformed."""
    if not isinstance(value, str) or value.count("/") != 1:
        return None, None
    room, actuator = value.split("/", 1)
    return room.strip(), actuator.strip()
