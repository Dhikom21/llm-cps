"""
Demo backend — small Flask service that turns chat prompts into ColonyOS
function specs and waits for the executor's response.

Run:
    python demo_backend.py

Requires env vars (see demo_README.md).
"""
import os
import sys
import time

from flask import Flask, request, jsonify
from flask_cors import CORS

try:
    from pycolonies import colonies_client
except ImportError:
    print("pycolonies is not installed. Run: pip install -r demo_requirements.txt")
    sys.exit(1)


COLONY_NAME    = os.environ.get("COLONIES_COLONY_NAME", "d7065e")
COLONY_PRVKEY  = os.environ.get("COLONIES_PRVKEY")
SUBMITTER_NAME = os.environ.get("COLONIES_USER_NAME", "demo-backend")
WAIT_TIMEOUT_S = int(os.environ.get("DEMO_WAIT_TIMEOUT", "30"))

if not COLONY_PRVKEY:
    print("COLONIES_PRVKEY is not set. See demo_README.md for setup.")
    sys.exit(1)


app = Flask(__name__)
CORS(app, origins=["http://localhost:*", "file://*"])

client = colonies_client()


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/chat")
def chat():
    """Accept a prompt, submit it as a ColonyOS function spec, wait for the
    executor to complete it, return the executor's output."""
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "missing 'prompt'"}), 400

    spec = {
        "conditions": {
            "colonyname":  COLONY_NAME,
            "executortype": "demo-agent",
        },
        "funcname":    "agent_cycle",
        "args":        [prompt],
        "maxwaittime": 30,
        "maxexectime": 60,
        "maxretries":  0,
        "label":       f"chat: {prompt[:40]}",
    }

    try:
        process = client.submit(spec, COLONY_PRVKEY)
    except Exception as e:
        return jsonify({"error": f"submit failed: {e}"}), 500

    process_id = process.processid
    print(f"[backend] submitted process {process_id[:12]}... prompt={prompt!r}")

    # Poll for completion
    deadline = time.time() + WAIT_TIMEOUT_S
    while time.time() < deadline:
        try:
            p = client.get_process(process_id, COLONY_PRVKEY)
        except Exception as e:
            return jsonify({"error": f"get_process failed: {e}",
                            "process_id": process_id}), 500

        state = getattr(p, "state", None)
        if state == 2:        # SUCCESS in pycolonies
            output = "".join(getattr(p, "output", []) or [])
            print(f"[backend]  → success: {output[:120]}")
            return jsonify({"status": "success",
                            "output": output,
                            "process_id": process_id})
        if state == 3:        # FAILED
            err = "".join(getattr(p, "errors", []) or [])
            print(f"[backend]  ✗ failed: {err[:120]}")
            return jsonify({"status": "failed",
                            "output": err or "(no error message)",
                            "process_id": process_id}), 200
        time.sleep(0.5)

    return jsonify({"status": "timeout",
                    "output": f"no response within {WAIT_TIMEOUT_S}s",
                    "process_id": process_id}), 200


if __name__ == "__main__":
    print(f"[backend] starting on :5000  colony={COLONY_NAME}")
    app.run(host="0.0.0.0", port=5000, debug=False)
