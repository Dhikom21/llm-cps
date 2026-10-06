"""
twin.py — everything that talks to BuildSim, in one place.

Both agents, the hardware driver and the evacuation service all need to
register equipment and push values. Rather than four copies of the same REST
calls, they share these.

One rule throughout: **a failed write to the twin is never fatal.** BuildSim is
a view. If it is down, the room must still be controlled correctly — so every
function here swallows network errors and returns a default. The things the
control loop genuinely depends on (the sensor and the relay) are local to the
Pi and involve no network at all.

Equipment IDs are derived from the room name, so a new room needs no new
constants:

    pi-temp-A109      / pi-temp-A109-val     temperature  (sensor)
    pi-heater-A109    / pi-heater-A109-state heater       (actuator)
    pi-alarm-A109     / pi-alarm-A109-state  buzzer       (actuator)
    pi-smoke-A109     / pi-smoke-A109-val    smoke        (sensor, twin-driven)
"""
import os

import requests

import rooms

BUILDSIM = os.environ.get("BUILDSIM_URL", "http://localhost:9090")
HTTP_TIMEOUT = float(os.environ.get("TWIN_TIMEOUT", "2.0"))

CLEAN_AIR_V = 0.10

_warned = False


def _warn(err):
    """Complain once, not every cycle. Repeated identical log lines hide the
    one line that mattered."""
    global _warned
    if not _warned:
        print(f"[twin] BuildSim unreachable at {BUILDSIM} ({err}) — "
              f"continuing without it")
        _warned = True


# ---------------- id helpers ----------------
def temp_eq(room):   return f"pi-temp-{room}"
def temp_val(room):  return f"pi-temp-{room}-val"
def heat_eq(room):   return f"pi-heater-{room}"
def heat_st(room):   return f"pi-heater-{room}-state"
def alarm_eq(room):  return f"pi-alarm-{room}"
def alarm_st(room):  return f"pi-alarm-{room}-state"
def smoke_eq(room):  return f"pi-smoke-{room}"
def smoke_val(room): return f"pi-smoke-{room}-val"


def actuator_state_id(room, actuator):
    return heat_st(room) if actuator == "heater" else alarm_st(room)


# ---------------- low-level ----------------
def _post(path, payload):
    try:
        requests.post(f"{BUILDSIM}{path}", json=payload, timeout=HTTP_TIMEOUT)
        return True
    except requests.RequestException as e:
        _warn(e)
        return False


def _put(path, payload):
    try:
        requests.put(f"{BUILDSIM}{path}", json=payload, timeout=HTTP_TIMEOUT)
        return True
    except requests.RequestException as e:
        _warn(e)
        return False


# ---------------- registration ----------------
def register_room(room):
    """Create this room's four pieces of equipment in the twin.

    Safe to call repeatedly — BuildSim keeps state in memory and loses it on
    restart, so re-registering on every agent start is how the floor plan
    repopulates itself.

    The `name` says whether an actuator is real, because on the floor plan a
    virtual heater and a real one look identical, and confusing the two while
    reading results would be an easy and expensive mistake.
    """
    level = rooms.level(room)
    real = rooms.is_real

    _post("/api/equipment", {
        "id": temp_eq(room), "name": f"{room} Temperature (real sensor)",
        "type": "temperature_sensor", "category": "monitoring",
        "level": level, "room": room, "status": "running"})
    _post(f"/api/equipment/{temp_eq(room)}/sensors", {
        "id": temp_val(room), "name": "Temperature", "type": "temperature",
        "data_type": "text", "unit": "°C", "value": "0.00"})

    _post("/api/equipment", {
        "id": heat_eq(room),
        "name": f"{room} Heater ({'real relay' if real(room,'heater') else 'virtual'})",
        "type": "radiator", "category": "hvac",
        "level": level, "room": room, "status": "running"})
    _post(f"/api/equipment/{heat_eq(room)}/actuators", {
        "id": heat_st(room), "name": "State", "type": "state",
        "data_type": "text", "value": "off"})

    _post("/api/equipment", {
        "id": alarm_eq(room),
        "name": f"{room} Alarm ({'real buzzer' if real(room,'buzzer') else 'virtual'})",
        "type": "fire_alarm_panel", "category": "safety",
        "level": level, "room": room, "status": "running"})
    _post(f"/api/equipment/{alarm_eq(room)}/actuators", {
        "id": alarm_st(room), "name": "State", "type": "state",
        "data_type": "text", "value": "off"})

    _post("/api/equipment", {
        "id": smoke_eq(room), "name": f"{room} Smoke (simulated)",
        "type": "smoke_detector", "category": "safety",
        "level": level, "room": room, "status": "running"})
    _post(f"/api/equipment/{smoke_eq(room)}/sensors", {
        "id": smoke_val(room), "name": "Smoke", "type": "smoke_level",
        "data_type": "text", "unit": "V", "value": f"{CLEAN_AIR_V:.3f}"})


def register_all():
    for room in rooms.names():
        register_room(room)
    _post("/api/equipment/notify", {})
    print(f"[twin] registered {', '.join(rooms.names())} at {BUILDSIM}")


# ---------------- reads ----------------
def read_smoke(room):
    """Smoke comes FROM the twin — the one arrow that points inward.

    The Pi has no analog input and no ADC, so this value is simulated. It is
    still the value the agent acts on, which is what makes a twin-side event
    able to sound a real buzzer.
    """
    try:
        r = requests.get(f"{BUILDSIM}/api/sensors/{smoke_val(room)}",
                         timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return round(float(r.json().get("value", CLEAN_AIR_V)), 3)
    except (requests.RequestException, ValueError, TypeError) as e:
        _warn(e)
        return CLEAN_AIR_V


# ---------------- writes ----------------
def publish_temp(room, value):
    _put(f"/api/sensors/{temp_val(room)}/value",
         {"data_type": "text", "value": f"{value:.2f}"})


def publish_actuator(room, actuator, state):
    _put(f"/api/actuators/{actuator_state_id(room, actuator)}/state",
         {"data_type": "text", "value": state})


def read_temp(room):
    """The temperature the twin currently shows for a room.

    Used by the monitor, which deliberately reads the published values rather
    than the sensors directly: a monitor should watch what the system claims,
    not re-derive it. If the agent stops publishing, the monitor sees a frozen
    signal — which is itself something worth catching.
    """
    try:
        r = requests.get(f"{BUILDSIM}/api/sensors/{temp_val(room)}",
                         timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return float(r.json().get("value"))
    except (requests.RequestException, ValueError, TypeError) as e:
        _warn(e)
        return None


def read_actuator(room, actuator):
    """'on' | 'off' | None — the state the twin shows for an actuator."""
    try:
        r = requests.get(
            f"{BUILDSIM}/api/actuators/{actuator_state_id(room, actuator)}",
            timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        body = r.json()
        return body.get("state") or body.get("value")
    except (requests.RequestException, ValueError, TypeError) as e:
        _warn(e)
        return None


def get_alerts():
    try:
        r = requests.get(f"{BUILDSIM}/api/alerts", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, list) else body.get("alerts", [])
    except (requests.RequestException, ValueError, TypeError):
        return []


def merge_alerts(prefix, cards):
    """Replace only the cards whose id starts with `prefix`, keeping others.

    /api/alerts replaces the entire collection on every write, so two processes
    writing it would erase each other — the evacuation service owns 'fire-*'
    cards and the monitor owns 'spec-*' ones. Read, substitute our own, write
    back.

    This is read-modify-write and therefore racy: if both write in the same
    instant one update is lost. Acceptable here because both rewrite their full
    set every cycle, so a lost update is corrected within seconds. Worth
    knowing rather than discovering during a demo.
    """
    keep = [c for c in get_alerts()
            if not str(c.get("id", "")).startswith(prefix)]
    _put("/api/alerts", keep + list(cards))


def set_smoke(room, volts):
    """Used by inject_fire.py. Raises on failure — a fault injector that
    silently does nothing is worse than none, because you would sit waiting
    for a response to an event that never happened."""
    r = requests.put(f"{BUILDSIM}/api/sensors/{smoke_val(room)}/value",
                     json={"data_type": "text", "value": f"{volts:.3f}"},
                     timeout=HTTP_TIMEOUT)
    r.raise_for_status()
