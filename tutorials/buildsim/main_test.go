package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestStepClosesTheFeedbackLoop(t *testing.T) {
	var written struct {
		DataType string `json:"data_type"`
		Value    string `json:"value"`
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/actuators/A109-set", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Fatalf("actuator method = %s", r.Method)
		}
		_ = json.NewEncoder(w).Encode(actuator{ID: "A109-set", State: "25"})
	})
	mux.HandleFunc("/api/sensors/A109-temp/value", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPut {
			t.Fatalf("sensor method = %s", r.Method)
		}
		if got := r.Header.Get("Content-Type"); got != "application/json" {
			t.Errorf("content type = %q", got)
		}
		if err := json.NewDecoder(r.Body).Decode(&written); err != nil {
			t.Fatal(err)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "updated", "version": 1})
	})
	server := httptest.NewServer(mux)
	defer server.Close()

	client := &http.Client{Timeout: time.Second}
	setpoint, temperature, err := step(client, server.URL, 18)
	if err != nil {
		t.Fatal(err)
	}
	if setpoint != 25 || temperature != 19.4 {
		t.Fatalf("step = setpoint %.1f, temperature %.1f", setpoint, temperature)
	}
	if written.DataType != "text" || written.Value != "19.4" {
		t.Fatalf("sensor write = %+v", written)
	}
}

func TestStepReportsInvalidActuatorState(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(actuator{ID: "A109-set", State: "not-a-number"})
	}))
	defer server.Close()

	_, _, err := step(&http.Client{Timeout: time.Second}, server.URL, 18)
	if err == nil {
		t.Fatal("invalid actuator state was accepted")
	}
}
