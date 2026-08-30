#!/usr/bin/env bash
# Print the current temperature reading directly from BuildSim.
BASE=${1:-http://127.0.0.1:9090}

if ! resp=$(curl --silent --show-error "$BASE/api/sensors/A109-temp"); then
  echo "cannot reach $BASE -- is BuildSim running? (buildsim start --port 9090)" >&2
  exit 1
fi

echo "$resp" | python3 -c '
import sys, json
s = json.load(sys.stdin)
if "error" in s:
    sys.exit("hvac-A109 not found -- run ./setup.sh first")
print(s["value"], s["unit"])
'
