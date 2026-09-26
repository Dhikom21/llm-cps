# Using BuildSim

BuildSim is the building provided for the project: a 3D viewer and a REST API in a single
binary. It **holds the building state** (rooms, equipment, sensor values, actuator states);
the project reads and writes that state. This tutorial runs BuildSim, puts a sensor and an
actuator in a room, and connects them with a **very simple physics simulator** so that
actuator commands change the sensor readings, the closed loop at the heart of the project.

<figure class="diagram">
<img src="figures/buildsim-fig01.svg" alt="BuildSim sits in the middle: the browser viewer connects over WebSocket, and the project's processes read and write state over the REST API">
<figcaption><em>BuildSim holds the shared state. The browser viewer connects over WebSocket; the project's own processes read and write that state over the REST API.</em></figcaption>
</figure>

For the full endpoint reference, see `buildingsim/docs/api/`; for how the API maps onto the
lab architecture, see `buildingsim/docs/lab-quickstart.md`.

---

## Part 1 — Start the BuildingSim server 

BuildSim is a single Go binary. From the D7065E repository root, build and
start it:

```bash
go build -o buildsim ./cmd
./buildsim start --port 9090
```

Open <http://127.0.0.1:9090> in a current browser. The 3D building appears. The runtime state is **in memory**, so it resets every time BuildSim
restarts; keep a small script to recreate the equipment.

Alternatively, run the supplied container setup from the `buildingsim/` directory:

```bash
docker compose up --build
```

The browser still uses <http://127.0.0.1:9090>. Student services in the same Compose file
use `http://buildsim:9090`; `127.0.0.1` inside a container refers to that container, not to
the laptop or to BuildSim.

---

## Part 2 — Put a sensor and an actuator in a room

Equipment is placed using both its floor and room name (for example `level0` and `A109`).
Create the equipment, then add a sensor and an actuator. Children created this way **or in a
bulk request** are addressable through `/api/sensors/{id}` and `/api/actuators/{id}`:

```bash
BASE=http://127.0.0.1:9090

curl -X POST $BASE/api/equipment -H 'Content-Type: application/json' -d \
  '{"id":"hvac-A109","name":"HVAC A109","type":"ac_unit","category":"hvac","level":"level0","room":"A109","status":"running"}'

curl -X POST $BASE/api/equipment/hvac-A109/sensors -H 'Content-Type: application/json' -d \
  '{"id":"A109-temp","name":"Temperature","type":"temperature","data_type":"text","unit":"°C","value":"18.0"}'

curl -X POST $BASE/api/equipment/hvac-A109/actuators -H 'Content-Type: application/json' -d \
  '{"id":"A109-set","name":"Setpoint","type":"setpoint","state":"21"}'
```

These three commands are bundled in [`buildsim/setup.sh`](buildsim/setup.sh).

Read either the sensor directly or its parent equipment:

```bash
curl -s "$BASE/api/sensors/A109-temp"
curl -s "$BASE/api/equipment/hvac-A109"
```

To print just the temperature value, use [`buildsim/gettemp.sh`](buildsim/gettemp.sh) (it
reports clearly if BuildSim is not running or the equipment was never created):

```bash
cd tutorials/buildsim
./gettemp.sh http://127.0.0.1:9090
# 18.0 °C
```

Sensor values and actuator states are always **text strings** (`"18.0"`, `"21"`), so the
code parses and formats them as numbers.

---

## Part 3 — A very simple physics simulator (close the loop)

The simulator is the program that makes actuator commands affect future sensor readings.
This one models a single idea: **the room temperature drifts toward the heating setpoint.**
Each tick it reads the setpoint actuator from BuildSim, nudges the temperature toward it,
and writes the new temperature back to the sensor.

`main.go`:

```go
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

const defaultBaseURL = "http://127.0.0.1:9090"

type actuator struct {
	ID    string `json:"id"`
	State string `json:"state"`
}

func main() {
	baseURL := strings.TrimRight(os.Getenv("BUILDSIM_URL"), "/")
	if baseURL == "" {
		baseURL = defaultBaseURL
	}
	client := &http.Client{Timeout: 5 * time.Second}
	temp := 18.0
	for {
		setpoint, next, err := step(client, baseURL, temp)
		if err != nil {
			log.Fatal(err)
		}
		temp = next
		fmt.Printf("setpoint %.1f -> temp %.1f\n", setpoint, temp)
		time.Sleep(2 * time.Second)
	}
}

func step(client *http.Client, baseURL string, temp float64) (float64, float64, error) {
	// 1. Read the heating setpoint from BuildSim.
	resp, err := client.Get(baseURL + "/api/actuators/A109-set")
	if err != nil {
		return 0, temp, fmt.Errorf("cannot reach BuildSim at %s: %w", baseURL, err)
	}
	if resp.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
		return 0, temp, fmt.Errorf("cannot read actuator: %s (did you run ./setup.sh?)", resp.Status)
	}
	var current actuator
	decodeErr := json.NewDecoder(resp.Body).Decode(&current)
	_ = resp.Body.Close()
	if decodeErr != nil {
		return 0, temp, fmt.Errorf("decode actuator: %w", decodeErr)
	}
	setpoint, err := strconv.ParseFloat(current.State, 64)
	if err != nil {
		return 0, temp, fmt.Errorf("actuator A109-set has invalid state %q: %w", current.State, err)
	}

	// 2. Very simple physics: move 20% of the way toward the setpoint.
	temp += 0.2 * (setpoint - temp)

	// 3. Write the new temperature back.
	body, err := json.Marshal(map[string]string{
		"data_type": "text", "value": fmt.Sprintf("%.1f", temp),
	})
	if err != nil {
		return 0, temp, fmt.Errorf("encode sensor value: %w", err)
	}
	req, err := http.NewRequest(http.MethodPut,
		baseURL+"/api/sensors/A109-temp/value", bytes.NewReader(body))
	if err != nil {
		return 0, temp, fmt.Errorf("create sensor request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	put, err := client.Do(req)
	if err != nil {
		return 0, temp, fmt.Errorf("cannot write to BuildSim: %w", err)
	}
	_, _ = io.Copy(io.Discard, put.Body)
	_ = put.Body.Close()
	if put.StatusCode != http.StatusOK {
		return 0, temp, fmt.Errorf("BuildSim rejected the write (%s), did you run ./setup.sh?", put.Status)
	}
	return setpoint, temp, nil
}
```

The complete runnable example, `main.go`, `go.mod`, and `setup.sh`, is in the
[`buildsim/`](buildsim/) folder. In another terminal, from the D7065E repository
root, create the equipment and start the simulator:

```bash
cd tutorials/buildsim
./setup.sh          # create hvac-A109 with its sensor and actuator
go run .            # start the simulator
# setpoint 21.0 -> temp 18.6
# setpoint 21.0 -> temp 19.1
# setpoint 21.0 -> temp 19.5  ...converging on 21
```

The program uses a five-second HTTP timeout instead of waiting forever for a
failed service. Set `BUILDSIM_URL` when BuildSim uses another address, for
example `BUILDSIM_URL=http://buildsim:9090 go run .` inside Compose.

Now **close the loop**: manually change the setpoint from another terminal and watch the
temperature follow it.

```bash
curl -X PUT http://127.0.0.1:9090/api/actuators/A109-set/state \
  -H 'Content-Type: application/json' -d '{"state": "25"}'
# the simulator now drives temp up toward 25
```

The `curl` command is a manual workshop probe, not the final project architecture. In the
project, an autonomous decision service sends a command to an independently deployable
**actuator service**; that service validates and applies the state to BuildSim. Keeping those
roles separate makes device failure, stale commands, retries, and authority testable.

That is the whole idea of a cyber-physical loop: an **actuator** command (the setpoint)
changes the **physical** model (the temperature), which changes the next **sensor** reading,
which an autonomous agent would read to decide the next command. A real simulator adds more
effects (occupancy, heat loss, CO₂), but the shape stays exactly this.

---

## Part 4 — See it in the 3D viewer (optional)

The browser viewer is driven through a **session**. The modern viewer shows the first eight
characters of its session ID at the upper left; click the chip to copy the full ID. You can
also find the most recently active session through the API, then colour the room:

```bash
SID=$(curl -s http://127.0.0.1:9090/api/sessions | python3 -c '
import sys,json
s=sorted(json.load(sys.stdin),key=lambda x:x.get("last_ws_active",""))
print(s[-1]["id"] if s else "")')
if [ -z "$SID" ]; then
  echo "No viewer session found; open http://127.0.0.1:9090 first" >&2
  exit 1
fi
curl -X PUT http://127.0.0.1:9090/api/sessions/$SID/highlights \
  -H 'Content-Type: application/json' \
  -d '[{"level":"level0","room":"A109","color":"#e53935","opacity":0.5}]'
```

Use `level` and `room` in new code. The legacy `room_id` field is accepted, but numeric IDs
can repeat between floors and therefore require a level qualifier.

Highlights, coverage zones, and the viewport all update live over WebSocket, so a dashboard
is largely a matter of mapping state to colours. See `buildingsim/docs/api/sessions.md` for
the full session API.

---

## Part 5 — Show physical state, fire, movement, and doors

A sensor is an observation from one device. The simulator's current room temperature is
physical state, and a predicted fire risk is an estimate. Publish state and estimates as
**room layers** instead of inventing a sensor to hold each value:

```bash
curl -X PUT http://127.0.0.1:9090/api/room-layers \
  -H 'Content-Type: application/json' -d '[{
  "id":"temperature","label":"Simulated room temperature","unit":"°C",
  "source":"simulation truth","minimum":18,"maximum":40,
  "values":{"level0/A109":38.2,"level0/A110":29.6}
}]'
```

The viewer also accepts renderer instructions for `fire`, `smoke`, `gas`, `sprinkler`,
`water`, and `warning` effects. These instructions do not simulate anything: the physical
model must still calculate how the phenomenon changes and publish each new snapshot.

Run the complete example from the `buildingsim/` directory:

```bash
./examples/api/scenario/show_visual_effects.sh
```

Then open
<http://127.0.0.1:9090/?layer=temperature&floor=level0&room=A109>. The example combines a
temperature layer, animated fire and smoke, an active sprinkler, exact equipment positions,
a person, a cleaning robot, room lighting, decision alerts, an exterior entrance, a fire
exit, and a locked door. Because the A109 door is linked to its walkable entry node, routes
through that door are unavailable until it is unlocked.

<p>
  <img src="../buildingsim/docs/images/visual-effects-floor.png" width="560" alt="BuildSim showing a room temperature layer, fire, smoke, sprinkler response, mobile entities, and door state">
</p>

The full payloads and semantics are in the [visualization
API](../buildingsim/docs/api/visualization.md) and [doors and locks
API](../buildingsim/docs/api/doors.md). Clear transient visual state with:

```bash
./examples/api/scenario/clear_visual_effects.sh
```

---

## Things to remember

- **Values are text.** Sensor `value` and actuator `state` are strings; parse and format them.
- **Qualify rooms.** Use `level` plus room name (`level0`, `A109`), or the canonical
  `level0/A109` map key. Names and numeric IDs can repeat on different floors.
- **Mutations notify automatically.** Successful equipment, sensor, and actuator writes
  update the version and refresh connected viewers. Normal clients must not call the legacy
  `/api/equipment/notify` endpoint.
- **No persistence, no physics.** BuildSim stores the current state only and does not model
  physics, the simulator above is the project's job.
- **State is not a sensor.** A room layer may be simulation truth or an estimate; a sensor
  remains a device observation, and an effect is only a rendering instruction.
- **Closed is not locked.** A closed but unlocked door remains routable. Locked, jammed, or
  blocked doorway nodes are excluded from walkable routes.
