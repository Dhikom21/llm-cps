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
import spec


def _reason(room, data, actuator, target):
    """A sentence citing the values the spec actually used.

    Generated from the same numbers that drove the decision, so the rule
    baseline cannot commit H3 by construction: its justification is derived
    from the reading rather than written alongside it.
    """
    smoke = data.get("smoke_v")
    temp  = data.get("temp_c")
    lo, hi = data.get("comfort_band", rooms.DEFAULT_BAND)
    theta = data.get("smoke_threshold", spec.THETA)

    if smoke is not None and smoke >= theta:
        return (f"{room} smoke {smoke:.2f} V at or above threshold {theta} "
                f"[phi_safety, phi_response]")
    if actuator == "buzzer":
        return f"{room} smoke {smoke:.2f} V below threshold [phi_no_false_alarm]"
    if target == "on":
        return f"{room} temp {temp:.2f} below {lo} [phi_comfort_low]"
    return f"{room} temp {temp:.2f} above {hi} [phi_comfort_high]"


def decide(room, data, state):
    """Actions for ONE room, derived from the specification.

    This used to be a hand-written ladder of conditions that happened to agree
    with the safety properties. It now asks spec.required_state() what the
    properties DEMAND and emits whatever differs from the present state — so
    the controller and the thing that verifies it are generated from one
    source and cannot drift apart.

    Where the spec returns None the situation is unconstrained — a room inside
    its comfort band, or one with no sensor reading. The rule does nothing
    there. That gap is precisely the space a language model is entitled to
    operate in, and naming it is more useful than filling it with an arbitrary
    default.
    """
    required = spec.required_state(data)

    actions = []
    for actuator in ("heater", "buzzer"):
        target = required.get(actuator)
        if target is None:                      # unconstrained by the spec
            continue
        if state.get(actuator) == target:       # already satisfied
            continue
        actions.append({
            "actuator": rooms.actuator_id(room, actuator),
            "state": target,
            "reason": _reason(room, data, actuator, target),
        })

    # Safety first in the emitted order too, so a truncated run still does the
    # more important thing first.
    actions.sort(key=lambda a: 0 if a["state"] == "off"
                 and a["actuator"].endswith("heater") else 1)
    return actions
