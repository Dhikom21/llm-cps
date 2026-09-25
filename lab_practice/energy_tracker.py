"""
energy_tracker.py -- Phase 1 (part 1 of 2): the "measuring stick".

Watches the heater's on/off state in BuildSim once a second and accumulates
ENERGY USED. Energy = power x time-on. It writes a running total to a NEW
table `energy_log` (created here; nothing existing is modified) and tags each
run with a `controller` label so you can later compare, e.g., "baseline" vs
"smart".

It works no matter WHO is driving the heater -- manual curl, the level5 rule
agent, the LLM agent, or the baseline controller we build next. It only
observes; it never commands.

Energy model (a lab assumption): the heater draws HEATER_KW kilowatts while on.
The absolute number does not matter for the comparison -- baseline and smart
use the SAME assumption, so the % difference is fair.

Prerequisites:
    docker compose up -d                # TimescaleDB
    # a heater must exist in BuildSim, i.e. one of:
    #   python level3_sim_loop.py   OR   python level4_sensor.py
    #   OR the baseline controller (next file)

Run (tag the session):
    CONTROLLER=baseline python energy_tracker.py
    #  ... or ...
    CONTROLLER=smart    python energy_tracker.py

Inspect:
    python energy_report.py             # (built next, part 2)
"""
import os
import signal
import sys
import time

import requests

import db   # reused read-only (we only call db.connect())

BASE          = "http://localhost:9090"
HEATER_EQ_ID  = "level4-heater-A109"          # equipment to poll
ROOM          = "A109"
HEATER_KW     = 2.0                            # assumed heater power (kW)
POLL_S        = 1.0                            # sample once a second
CONTROLLER    = os.environ.get("CONTROLLER", "unknown")   # session label

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS energy_log (
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    controller     TEXT,
    room           TEXT,
    heater_on      BOOLEAN,
    power_kw       DOUBLE PRECISION,
    interval_kwh   DOUBLE PRECISION,   -- energy used since the last sample
    cumulative_kwh DOUBLE PRECISION    -- running total for THIS session
);
"""

INSERT_SQL = """
    INSERT INTO energy_log
        (controller, room, heater_on, power_kw, interval_kwh, cumulative_kwh)
    VALUES (%s, %s, %s, %s, %s, %s)
"""

# ---- connect + ensure our new table exists ----
conn = db.connect(autocommit=True)
with conn.cursor() as cur:
    cur.execute(CREATE_SQL)

cumulative_kwh = 0.0
samples = 0

def read_heater_on():
    """Return True/False for the heater state, or None if not reachable/found."""
    try:
        r = requests.get(f"{BASE}/api/equipment/{HEATER_EQ_ID}", timeout=2.0)
        if r.status_code != 200:
            return None
        acts = r.json().get("actuators", [])
        if not acts:
            return None
        return acts[0]["state"] == "on"
    except requests.RequestException:
        return None

def cleanup(*_):
    print(f"\nstopping. controller={CONTROLLER!r}  "
          f"total energy = {cumulative_kwh:.4f} kWh over {samples} samples.")
    try: conn.close()
    except Exception: pass
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

print(f"energy tracker running. controller={CONTROLLER!r}, heater={HEATER_KW} kW, "
      f"sampling every {POLL_S:.0f}s. Ctrl-C to stop.")

while True:
    on = read_heater_on()
    if on is None:
        print(f"[{time.strftime('%H:%M:%S')}] heater '{HEATER_EQ_ID}' not found "
              f"(is a sim/controller running?)")
        time.sleep(POLL_S)
        continue

    # energy this interval: kWh = kW * hours; POLL_S seconds = POLL_S/3600 hours
    interval_kwh = (HEATER_KW * (POLL_S / 3600.0)) if on else 0.0
    cumulative_kwh += interval_kwh
    samples += 1

    with conn.cursor() as cur:
        cur.execute(INSERT_SQL,
                    [CONTROLLER, ROOM, on, HEATER_KW, interval_kwh, cumulative_kwh])

    if samples % 10 == 0:
        print(f"[{time.strftime('%H:%M:%S')}] heater={'ON ' if on else 'off'}  "
              f"cumulative={cumulative_kwh:.4f} kWh")

    time.sleep(POLL_S)
