"""
edge_agent_rules.py — the baseline decision, on its own.

Kept in its own module for two reasons:

  1. edge_agent.py runs a control loop at import time, so the LLM agent cannot
     import it just to borrow the rule. Putting the rule here lets both use it.
  2. It makes the comparison exact. When the model is unreachable, the LLM
     agent falls back to THIS function — the same code the baseline runs, not a
     second implementation that might quietly differ.

decide() is a pure function of its inputs: no hardware, no network, no hidden
state. Every case can be checked at a desk in a second:

    >>> decide("A109", {"temp_c": 21.0, "smoke_v": 0.1,
    ...                 "comfort_band": [22.0, 24.0], "smoke_threshold": 1.0},
    ...        {"heater": "off", "buzzer": "off"})
    [{'actuator': 'A109/heater', 'state': 'on', 'reason': 'temp 21.00 below 22.0'}]

Keeping the thinking separate from the doing is what makes control code
testable — and it is what makes swapping in a language model a one-line change
rather than a rewrite.
"""
import rooms


def decide(room, data, state):
    """Actions for ONE room. Returns only CHANGES.

    Re-commanding an actuator already in the right state would wear out a
    mechanical relay for nothing, so a no-op returns an empty list.
    """
    actions = []
    temp  = data["temp_c"]
    smoke = data["smoke_v"]
    lo, hi = data["comfort_band"]
    threshold = data.get("smoke_threshold", rooms.SMOKE_THRESHOLD)
    heater_on = state["heater"] == "on"
    buzzer_on = state["buzzer"] == "on"

    # 1. SAFETY. First branch on purpose: the ordering IS the policy, and
    #    writing it here makes it visible and reviewable rather than buried in
    #    a compound condition. Smoke outranks comfort however cold the room is.
    if smoke >= threshold:
        if heater_on:
            actions.append({"actuator": rooms.actuator_id(room, "heater"),
                            "state": "off",
                            "reason": f"{room} smoke {smoke:.2f} V at or above "
                                      f"threshold {threshold}"})
        if not buzzer_on:
            actions.append({"actuator": rooms.actuator_id(room, "buzzer"),
                            "state": "on",
                            "reason": f"{room} smoke {smoke:.2f} V at or above "
                                      f"threshold {threshold}"})
        return actions

    if buzzer_on:
        actions.append({"actuator": rooms.actuator_id(room, "buzzer"),
                        "state": "off",
                        "reason": f"{room} smoke {smoke:.2f} V back below "
                                  f"threshold"})

    # 2. COMFORT. A missing reading is not a reason to act — None here means
    #    the sensor for this room was not found, and guessing would be worse
    #    than doing nothing.
    if temp is None:
        return actions

    if temp < lo and not heater_on:
        actions.append({"actuator": rooms.actuator_id(room, "heater"),
                        "state": "on",
                        "reason": f"{room} temp {temp:.2f} below {lo}"})
    elif temp > hi and heater_on:
        actions.append({"actuator": rooms.actuator_id(room, "heater"),
                        "state": "off",
                        "reason": f"{room} temp {temp:.2f} above {hi}"})

    return actions
