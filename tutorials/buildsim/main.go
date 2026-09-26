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
