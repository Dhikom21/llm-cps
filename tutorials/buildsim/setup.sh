#!/usr/bin/env bash
# Create the tutorial HVAC setup atomically. Re-running this script is safe.
BASE=${1:-http://127.0.0.1:9090}

curl --fail --silent --show-error -X POST "$BASE/api/equipment/bulk" \
  -H 'Content-Type: application/json' -d '[{
    "id":"hvac-A109","name":"HVAC A109","type":"ac_unit","category":"hvac",
    "level":"level0","room":"A109","status":"running",
    "sensors":[{"id":"A109-temp","name":"Temperature","type":"temperature","data_type":"text","unit":"°C","value":"18.0"}],
    "actuators":[{"id":"A109-set","name":"Setpoint","type":"setpoint","state":"21"}]
  }]' >/dev/null

echo "hvac-A109 is ready (sensor A109-temp, actuator A109-set)"
