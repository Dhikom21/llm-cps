"""
bus.py — the MQTT message bus, in one place.

Four processes now talk to each other instead of being one program, and they
do it through a broker rather than by calling each other. None of them knows
the others exist; they know only topic names.

    pi_sensor.py    publishes  sensors/{level}/{room}/reading
    pi_consumer.py  subscribes sensors/#                 -> SQLite
    the agent       subscribes sensors/# and actuators/#
                    publishes  commands/{room}/{actuator}
    pi_actuator.py  subscribes commands/#                -> the GPIO pins
                    publishes  actuators/{room}/{actuator}/state

--------------------------------------------------------------------------
Why a broker and not function calls
--------------------------------------------------------------------------
A direct call couples the caller to the callee: the agent would have to know
the database exists, and a slow write would stall the control loop. Publishing
to a topic couples nothing. Add a dashboard, a logger, a second analyser — the
publisher never changes and never learns they are there.

The cost is honest and worth stating: the broker is now on the control path.
If Mosquitto dies, commands stop flowing. It runs on the Pi's own localhost,
so it is about as reliable as a function call, but it is no longer impossible
for it to fail — and the actuator process is written so that losing the bus
leaves the hardware in a safe state rather than its last state.

--------------------------------------------------------------------------
Topic design
--------------------------------------------------------------------------
Topics are hierarchical and wildcards work on the levels, so `sensors/#` means
"everything below sensors". Room and actuator names are IN the topic rather
than only in the payload, which lets a subscriber filter before parsing and
makes `mosquitto_sub -t 'commands/#' -v` a usable debugging tool.

Every payload is JSON with a `ts` field, so a late or replayed message can be
recognised as late.
"""
import json
import os
import threading
import time

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

# ---------------- topics ----------------
def reading_topic(level, room):
    return f"sensors/{level}/{room}/reading"


def command_topic(room, actuator):
    return f"commands/{room}/{actuator}"


def state_topic(room, actuator):
    return f"actuators/{room}/{actuator}/state"


def result_topic(room, actuator):
    """The verdict on a command: applied, or refused and why.

    A command is fire-and-forget by nature, but the requester needs to KNOW
    whether the shield allowed it — in this project especially, because the
    refusal is fed back to the model and how it responds to being refused is
    one of the things being measured. So the actuator process answers on a
    result topic, and the requester waits briefly for the reply that carries
    its own cmd_id.
    """
    return f"results/{room}/{actuator}"


ALL_READINGS = "sensors/#"
ALL_COMMANDS = "commands/#"
ALL_STATES   = "actuators/#"
ALL_RESULTS  = "results/#"


class BusUnavailable(RuntimeError):
    pass


class Bus:
    """A thin wrapper over paho. Handlers receive (topic, dict)."""

    def __init__(self, client_id, on_lost=None):
        self.client_id = client_id
        self._handlers = []            # (topic_filter, callback)
        self._connected = threading.Event()
        self._on_lost = on_lost
        self._client = None

    # ---------------- lifecycle ----------------
    def connect(self, timeout=5.0):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise BusUnavailable(f"paho-mqtt is not installed: {exc}")

        # A unique client id per process. Brokers disconnect an existing client
        # that reconnects with an id already in use, so two copies of the same
        # service would silently knock each other offline all day.
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                   client_id=f"{self.client_id}-{os.getpid()}")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        try:
            self._client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        except OSError as exc:
            raise BusUnavailable(
                f"no broker at {MQTT_HOST}:{MQTT_PORT} ({exc}). "
                f"Start one with: sudo systemctl start mosquitto")
        self._client.loop_start()

        if not self._connected.wait(timeout):
            raise BusUnavailable(
                f"broker at {MQTT_HOST}:{MQTT_PORT} did not accept the "
                f"connection within {timeout:.0f}s")
        return self

    def close(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()

    # ---------------- callbacks ----------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            # Subscriptions are (re)made here rather than at subscribe() time,
            # so a reconnect after a broker restart restores them. Subscribing
            # once at startup would silently stop delivering after an outage.
            for topic, _ in self._handlers:
                client.subscribe(topic)
            self._connected.set()

    def _on_disconnect(self, client, userdata, *args):
        self._connected.clear()
        if self._on_lost:
            self._on_lost()

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (ValueError, UnicodeDecodeError):
            return                     # not ours, or corrupt; ignore quietly
        for topic, handler in self._handlers:
            if _matches(topic, msg.topic):
                try:
                    handler(msg.topic, payload)
                except Exception as exc:
                    # A handler that raises must not kill the network thread,
                    # which would leave the process alive but deaf.
                    print(f"[bus] handler for {msg.topic} failed: {exc}")

    # ---------------- use ----------------
    def subscribe(self, topic_filter, handler):
        self._handlers.append((topic_filter, handler))
        if self._client and self._connected.is_set():
            self._client.subscribe(topic_filter)
        return self

    def publish(self, topic, payload, retain=False):
        """Publish a dict as JSON. `ts` is added if absent.

        retain=True asks the broker to keep the last message on that topic and
        deliver it immediately to anyone who subscribes later. Used for
        actuator STATE, so a restarting agent learns the heater is already on
        without waiting for it to change.
        """
        if "ts" not in payload:
            payload = {**payload, "ts": time.time()}
        if not self._client:
            raise BusUnavailable("publish before connect")
        self._client.publish(topic, json.dumps(payload), qos=1, retain=retain)

    @property
    def connected(self):
        return self._connected.is_set()


def _matches(filter_, topic):
    """MQTT topic matching for the two wildcards: + one level, # the rest."""
    if filter_ == topic:
        return True
    f, t = filter_.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t):
            return False
        if part != "+" and part != t[i]:
            return False
    return len(f) == len(t)
