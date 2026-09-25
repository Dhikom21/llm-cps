"""
Tail the `decisions` audit table (lecture-4 §Audit Trail) — every Thought,
Action, Observation, Guardrail-block, Fallback, and Final by every agent.

Usage:
    python view_decisions.py            # last 30 decisions
    python view_decisions.py 100        # last 100
    python view_decisions.py 30 safety  # last 30 from agents whose id contains 'safety'
"""
import sys
import json

import db


limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
agent_like = sys.argv[2] if len(sys.argv) > 2 else None

sql = """
    SELECT ts, agent_id, step, tool, args, result, text, success
    FROM decisions
    {where}
    ORDER BY ts DESC
    LIMIT %s
"""
where = ""
params = []
if agent_like:
    where = "WHERE agent_id LIKE %s"
    params.append(f"%{agent_like}%")
params.append(limit)
sql = sql.format(where=where)


with db.connect() as conn, conn.cursor() as cur:
    cur.execute(sql, params)
    rows = list(reversed(cur.fetchall()))

if not rows:
    print("(no decisions logged yet — start an agent first)")
    sys.exit(0)

# Header
print(f"{'time':>8s}  {'agent':22s}  {'step':16s}  {'tool':14s}  {'ok':3s}  detail")
print("-" * 100)

for ts, agent_id, step, tool, args, result, text, success in rows:
    t = ts.strftime("%H:%M:%S")
    ok = "✓" if success else "✗"
    detail = ""
    if step == "thought":
        detail = (text or "")[:80]
    elif step == "action":
        detail = f"args={json.dumps(args)[:80]}" if args else ""
    elif step == "observation":
        if result:
            ok_inner = result.get("success")
            data = result.get("data", {})
            err  = result.get("error")
            detail = (f"err={err}" if err
                      else f"data={json.dumps(data)[:80]}")
    else:
        detail = (text or "")[:80]
    print(f"{t}  {agent_id:22s}  {step:16s}  {tool or '-':14s}  {ok:3s}  {detail}")
