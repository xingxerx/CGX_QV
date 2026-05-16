"""
Server endpoint tests.

Uses FastAPI's TestClient (synchronous) since the app has no real async I/O
in tests (LMDB reads return empty gracefully when the DB doesn't exist).
"""

import time
import pytest
from fastapi.testclient import TestClient

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import server as srv
from server import app

client = TestClient(app)


# ── /health ───────────────────────────────────────────────────────────────

def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── /metrics ─────────────────────────────────────────────────────────────

def test_metrics_shape():
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "active" in body
    assert "biometrics" in body
    assert isinstance(body["biometrics"], dict)


# ── /logs ─────────────────────────────────────────────────────────────────

def test_logs_returns_list():
    r = client.get("/logs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── /phase ───────────────────────────────────────────────────────────────

def test_phase_start_valid():
    r = client.post("/phase/1/start", json={"ticks": 100})
    assert r.status_code == 200
    assert r.json()["phase"] == 1

def test_phase_start_invalid_phase():
    r = client.post("/phase/9/start", json={"ticks": 100})
    assert r.status_code == 400

def test_phase_stop():
    client.post("/phase/2/start", json={"ticks": 50})
    r = client.post("/phase/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


# ── /export ───────────────────────────────────────────────────────────────

def test_export_json():
    r = client.get("/export?format=json")
    assert r.status_code == 200
    assert "exported_at" in r.json()

def test_export_csv():
    r = client.get("/export?format=csv")
    assert r.status_code == 200
    assert "key,value" in r.text

def test_export_unknown_format():
    r = client.get("/export?format=xml")
    assert r.status_code == 400


# ── /swarm ────────────────────────────────────────────────────────────────

def test_swarm_status_shape():
    r = client.get("/swarm/status")
    assert r.status_code == 200
    body = r.json()
    assert "swarm" in body
    assert "active_generation_size" in body

def test_swarm_spawn_returns_offspring():
    r = client.post("/swarm/spawn", json={"n": 4, "divergence_factor": 0.05})
    assert r.status_code == 200
    body = r.json()
    assert len(body["offspring"]) == 4

def test_swarm_spawn_invalid_n():
    r = client.post("/swarm/spawn", json={"n": 0, "divergence_factor": 0.05})
    assert r.status_code == 400

def test_swarm_evolve_without_spawn_fails():
    # Clear any active generation first
    srv._active_generation = []
    r = client.post("/swarm/evolve", json={"learning_rate": 0.1})
    assert r.status_code == 400

def test_swarm_full_cycle():
    spawn_r = client.post("/swarm/spawn", json={"n": 3, "divergence_factor": 0.05})
    assert spawn_r.status_code == 200

    evolve_r = client.post("/swarm/evolve", json={"learning_rate": 0.1})
    assert evolve_r.status_code == 200
    body = evolve_r.json()
    assert "winner" in body
    assert "parent" in body
    assert body["parent"]["generation"] > 0
