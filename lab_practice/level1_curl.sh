#!/usr/bin/env bash
# Level 1 — BuildSim alone, no code.
# Run this from Git Bash or WSL on Windows.
# Each step prints what it does, then runs it.

set -e
BASE=http://localhost:9090

echo "=== Building metadata ==="
curl -s "$BASE/api/building" | head -c 400; echo

echo "=== List rooms on level 0 (first 10) ==="
curl -s "$BASE/api/building/floors/level0" \
  | python3 -c "import sys,json; print('\n'.join(r['name'] for r in json.load(sys.stdin)['rooms'][:10]))"

echo
echo "=== Add a temperature sensor in room A109 ==="
curl -s -X POST "$BASE/api/equipment" -H 'Content-Type: application/json' -d '{
  "id": "demo-temp-A109", "name": "Demo Temp", "type": "temperature_sensor",
  "category": "monitoring", "level": "level0", "room": "A109", "status": "running"
}' | head -c 200; echo

curl -s -X POST "$BASE/api/equipment/demo-temp-A109/sensors" -H 'Content-Type: application/json' -d '{
  "id": "demo-temp-A109-val", "name": "Temperature",
  "type": "temperature", "data_type": "text", "unit": "C", "value": "22.0"
}' | head -c 200; echo

curl -s -X POST "$BASE/api/equipment/notify" > /dev/null
echo "Notify sent — check the browser, A109 should now have a thermometer icon."

echo
echo "=== Cycle the sensor value 5 times so you see updates ==="
for v in 22.5 23.1 24.0 24.8 25.5; do
  curl -s -X PUT "$BASE/api/sensors/demo-temp-A109-val/value" \
    -H 'Content-Type: application/json' -d "{\"data_type\":\"text\",\"value\":\"$v\"}" > /dev/null
  echo "set $v"
  sleep 1
done

echo
echo "=== Find the active browser session and zoom to A109 ==="
SESSION=$(curl -s "$BASE/api/sessions" | python3 -c "
import sys, json
s = json.load(sys.stdin)
s.sort(key=lambda x: x.get('last_ws_active',''), reverse=True)
print(s[0]['id'] if s else '')")
echo "Session: $SESSION"

if [ -n "$SESSION" ]; then
  echo "=== Look up A109's integer room_id from the floor data ==="
  ROOM_ID=$(curl -s "$BASE/api/building/floors/level0" | python3 -c "
import sys, json
for r in json.load(sys.stdin)['rooms']:
    if r['name'] == 'A109':
        print(r['id']); break")
  echo "A109 is room_id $ROOM_ID"

  curl -s -X PUT "$BASE/api/sessions/$SESSION/viewport" \
    -H 'Content-Type: application/json' \
    -d '{"room": "A109", "zoom": 2.5, "mode": "3d"}' > /dev/null
  echo "Browser should fly to A109."

  curl -s -X PUT "$BASE/api/sessions/$SESSION/highlights" \
    -H 'Content-Type: application/json' \
    -d "[{\"room_id\": $ROOM_ID, \"color\": \"#ff0000\", \"opacity\": 0.6}]" > /dev/null
  echo "A109 highlighted red — same room as the sensor."
fi

echo
echo "=== Cleanup the demo sensor ==="
curl -s -X DELETE "$BASE/api/equipment/demo-temp-A109" > /dev/null
curl -s -X POST "$BASE/api/equipment/notify" > /dev/null
echo "Done. Demo sensor removed, browser refreshed."
