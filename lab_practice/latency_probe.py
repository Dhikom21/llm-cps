#!/usr/bin/env python3
"""
latency_probe.py -- measure REST latency for the Level 3 actuator loop.

It measures the actuator's reaction time in its TWO parts:

  [1] Per-poll GET latency  -- the network round-trip of a single poll (ms).
  [2] End-to-end (tight poll) -- PUT a command, then detect it as fast as
      possible. This ~= the pure network component (PUT + GET round-trips).

The REAL actuator in level3 polls only every POLL_INTERVAL seconds, so on top
of the network cost it waits 0..POLL_INTERVAL for its next poll to even fire.
That poll gap is usually the dominant term -- the point of the experiment.

Prerequisites:
  * BuildSim running at localhost:9090.
  * For the end-to-end test, run level3_sim_loop.py too (it registers the
    heater actuator). If the heater is absent, that test is skipped.

Run:
    python latency_probe.py
"""
import time
import statistics
import sys
import requests

BASE          = "http://localhost:9090"
ACT_EQ_ID     = "level3-heater-A109"
ACT_STATE_ID  = "level3-heater-A109-state"
POLL_INTERVAL = 0.5     # matches the sleep in level3's actuator() loop
N             = 50      # samples for the per-poll test


def timed_get(path):
    """GET a path and return (milliseconds, response)."""
    t0 = time.perf_counter()
    r = requests.get(f"{BASE}{path}", timeout=2.0)
    ms = (time.perf_counter() - t0) * 1000.0
    return ms, r


def measure_poll_latency():
    print("\n[1] Per-poll GET latency (the cost of one poll)")
    # Prefer the exact endpoint the actuator polls; fall back to /api/building.
    path = f"/api/equipment/{ACT_EQ_ID}"
    _, r = timed_get(path)
    if r.status_code != 200:
        path = "/api/building"
        print(f"    (heater not found; using {path} as a baseline)")

    samples = []
    for _ in range(N):
        ms, _ = timed_get(path)
        samples.append(ms)
        time.sleep(0.05)

    samples.sort()
    p95 = samples[max(0, int(0.95 * N) - 1)]
    print(f"    endpoint : {path}")
    print(f"    samples  : {N}")
    print(f"    min      : {min(samples):6.2f} ms")
    print(f"    median   : {statistics.median(samples):6.2f} ms")
    print(f"    mean     : {statistics.mean(samples):6.2f} ms")
    print(f"    p95      : {p95:6.2f} ms")
    print(f"    max      : {max(samples):6.2f} ms")


def set_state(state):
    requests.put(f"{BASE}/api/actuators/{ACT_STATE_ID}/state",
                 json={"state": state}, timeout=2.0)


def read_state():
    r = requests.get(f"{BASE}/api/equipment/{ACT_EQ_ID}", timeout=2.0)
    return r.json()["actuators"][0]["state"]


def measure_end_to_end(trials=6):
    print("\n[2] End-to-end command latency (PUT -> first observable via a tight poll)")
    try:
        read_state()
    except Exception:
        print("    heater actuator not found -- start level3_sim_loop.py first. Skipping.")
        return

    delays = []
    for t in range(trials):
        target = "on" if t % 2 == 0 else "off"
        set_state("off" if target == "on" else "on")   # force opposite first
        time.sleep(0.3)

        t0 = time.perf_counter()
        set_state(target)                               # the command we time
        while True:                                     # tight poll = detect ASAP
            if read_state() == target:
                break
            if time.perf_counter() - t0 > 5:
                break
        ms = (time.perf_counter() - t0) * 1000.0
        delays.append(ms)
        print(f"    trial {t + 1}: '{target}' observable after {ms:6.1f} ms")
        time.sleep(0.3)

    net = statistics.mean(delays)
    print(f"\n    network component (tight poll)   : {net:.1f} ms")
    print(f"    real actuator adds a poll gap of : 0 .. {POLL_INTERVAL*1000:.0f} ms"
          f"  (avg ~{POLL_INTERVAL*1000/2:.0f} ms)")
    print(f"    => real reaction time ~= {net:.0f} ms + up to {POLL_INTERVAL*1000:.0f} ms")
    print(f"    The poll gap, not the network, dominates -- this is why push (MQTT) wins.")


if __name__ == "__main__":
    try:
        requests.get(f"{BASE}/api/building", timeout=2.0)
    except Exception as e:
        print(f"Cannot reach BuildSim at {BASE}: {e}")
        print("Start BuildSim first (make run in ../buildingsim).")
        sys.exit(1)

    measure_poll_latency()
    measure_end_to_end()
    print()
