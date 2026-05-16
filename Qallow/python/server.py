"""
Qallow reasoning server — serves the REST API consumed by the native GUI (api_client.rs)
and by the veyn bridge.

Endpoints expected by api_client.rs:
  GET  /metrics
  GET  /logs
  POST /phase/{n}/start
  POST /phase/stop
  GET  /export
  POST /chat

Additional endpoint wired from qallow-veyn-bridge:
  POST /snapshot
"""

import json
import logging
import os
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import lmdb
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from agents.llm_adapter import LLMAdapter, LLMConfig
from asios_bridge import AsiosBridge
from swarm import (
    OffspringProfile as SwarmChild,
    SwarmState,
    evaluate as swarm_evaluate,
    feedback_to_parent,
    select_survivor,
    spawn,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
)
logger = logging.getLogger("QallowServer")

app = FastAPI(title="Qallow Reasoning Server", version="1.0.0")

LMDB_PATH = Path(__file__).parent / "../core/qallow-veyn-bridge/veyn_metrics"
SNAPSHOTS_DIR = Path(__file__).parent / "../../snapshots"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

AUDIT_LOG: list[dict] = []
PHASE_STATE: dict = {"active": False, "phase": None, "started_at": None}

_llm: Optional[LLMAdapter] = None
_asios: Optional[AsiosBridge] = None
_last_snapshot_at: float = 0.0

ASIOS_LMDB_PATH = Path(__file__).parent / "../core/qallow-veyn-bridge/asios_state"
_swarm = SwarmState(instance_id="swarm-000")
_active_generation: list[SwarmChild] = []
SNAPSHOT_MIN_INTERVAL: float = 55.0  # slightly under the bridge's 60 s cooldown


def get_asios() -> AsiosBridge:
    global _asios
    if _asios is None:
        _asios = AsiosBridge(ASIOS_LMDB_PATH)
    return _asios


def get_llm() -> LLMAdapter:
    global _llm
    if _llm is None:
        _llm = LLMAdapter(LLMConfig())
    return _llm


def read_lmdb_metrics() -> dict[str, float]:
    metrics: dict[str, float] = {}
    try:
        if not LMDB_PATH.exists():
            return metrics
        env = lmdb.open(str(LMDB_PATH), readonly=True, max_dbs=1)
        db = env.open_db(b"veyn_metrics")
        keys = [b"energy", b"risk", b"reward_mod", b"autonomy"]
        with env.begin(db=db) as txn:
            for k in keys:
                v = txn.get(k)
                if v:
                    metrics[k.decode()] = struct.unpack("<d", v)[0]
        env.close()
    except Exception as e:
        logger.warning(f"LMDB read failed: {e}")
    return metrics


def audit(action: str, detail: str = "") -> None:
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "detail": detail,
    }
    AUDIT_LOG.append(entry)
    if len(AUDIT_LOG) > 1000:
        AUDIT_LOG.pop(0)
    logger.info(f"[AUDIT] {action}: {detail}")


# ---------------------------------------------------------------------------
# Phase control
# ---------------------------------------------------------------------------

class PhaseStartRequest(BaseModel):
    ticks: int = 100


@app.post("/phase/{phase}/start")
async def start_phase(phase: int, body: PhaseStartRequest):
    if phase not in range(1, 6):
        raise HTTPException(status_code=400, detail="Phase must be 1–5")
    PHASE_STATE.update({"active": True, "phase": phase, "started_at": time.time()})
    audit("phase_start", f"phase={phase} ticks={body.ticks}")

    # Seed ASIOS with current biometric state at phase entry
    metrics = read_lmdb_metrics()
    if metrics:
        asios = get_asios()
        asios.observe(
            phase     = phase,
            reward    = metrics.get("reward_mod", 0.5),
            ethics    = 1.0 - metrics.get("risk", 0.5),
            coherence = metrics.get("reward_mod", 0.5),
            energy    = metrics.get("energy", 0.5),
            risk      = metrics.get("risk", 0.5),
        )

    return {"status": "started", "phase": phase, "ticks": body.ticks}


@app.post("/phase/stop")
async def stop_phase():
    prev = PHASE_STATE.get("phase")
    PHASE_STATE.update({"active": False, "phase": None, "started_at": None})
    audit("phase_stop", f"was phase={prev}")

    # Run one adaptation step at phase exit
    try:
        get_asios().adapt()
    except Exception as e:
        logger.warning(f"ASIOS adapt failed: {e}")

    return {"status": "stopped"}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@app.get("/metrics")
async def get_metrics():
    metrics = read_lmdb_metrics()
    uptime = (
        time.time() - PHASE_STATE["started_at"] if PHASE_STATE["started_at"] else 0.0
    )
    return {
        "phase": PHASE_STATE.get("phase"),
        "active": PHASE_STATE.get("active"),
        "uptime": round(uptime, 1),
        "overlay_stability": metrics.get("energy", 0.0),
        "ethics_score": 1.0 - metrics.get("risk", 0.0),
        "coherence": metrics.get("reward_mod", 0.0),
        "biometrics": metrics,
    }


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------

@app.get("/logs")
async def get_logs():
    return AUDIT_LOG[-200:]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@app.get("/export")
async def export_metrics(format: str = "json"):
    metrics = read_lmdb_metrics()
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "phase_state": PHASE_STATE,
        "audit_log_count": len(AUDIT_LOG),
    }
    if format == "json":
        return payload
    elif format == "csv":
        lines = ["key,value", *[f"{k},{v}" for k, v in metrics.items()]]
        return PlainTextResponse("\n".join(lines), media_type="text/csv")
    raise HTTPException(status_code=400, detail=f"Unknown format: {format}")


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str


@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        llm = get_llm()
        reply = llm.chat(req.message)
        audit("chat", f"len={len(req.message)}")
        return {"reply": reply}
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=503, detail=str(e))



# ---------------------------------------------------------------------------
# Swarm
# ---------------------------------------------------------------------------

class SwarmSpawnRequest(BaseModel):
    n: int = 5
    divergence_factor: float = 0.05


class SwarmEvolveRequest(BaseModel):
    learning_rate: float = 0.1


@app.post("/swarm/spawn")
async def swarm_spawn(req: SwarmSpawnRequest):
    global _active_generation
    if req.n < 1 or req.n > 32:
        raise HTTPException(status_code=400, detail="n must be 1–32")
    if req.divergence_factor <= 0 or req.divergence_factor > 1.0:
        raise HTTPException(status_code=400, detail="divergence_factor must be in (0, 1]")
    step = PHASE_STATE.get("current_step") or 0
    _active_generation = spawn(_swarm, req.n, req.divergence_factor, genesis_step=step)
    audit("swarm_spawn", f"gen={_swarm.generation} n={req.n} div={req.divergence_factor}")
    return {
        "generation": _swarm.generation,
        "spawned": req.n,
        "offspring": [vars(o) for o in _active_generation],
    }


@app.post("/swarm/evolve")
async def swarm_evolve(req: SwarmEvolveRequest):
    global _swarm, _active_generation
    if not _active_generation:
        raise HTTPException(status_code=400, detail="no active generation — call /swarm/spawn first")
    metrics = read_lmdb_metrics()
    ethics_score = 1.0 - metrics.get("risk", 0.5)
    eval_metrics = {
        "energy":       metrics.get("energy",      0.5),
        "risk":         metrics.get("risk",         0.5),
        "reward_mod":   metrics.get("reward_mod",   0.5),
        "ethics_score": ethics_score,
    }
    for child in _active_generation:
        swarm_evaluate(child, eval_metrics)
    winner = select_survivor(_active_generation)
    if winner is None:
        raise HTTPException(status_code=500, detail="evaluation produced no survivors")
    _swarm = feedback_to_parent(winner, _swarm, req.learning_rate)
    audit("swarm_evolve", f"gen={_swarm.generation} winner={winner.tag} fitness={winner.fitness}")
    _active_generation = []
    return {
        "generation": _swarm.generation,
        "winner": vars(winner),
        "parent": vars(_swarm),
    }


@app.get("/swarm/status")
async def swarm_status():
    return {
        "swarm": vars(_swarm),
        "active_generation_size": len(_active_generation),
        "offspring": [vars(o) for o in _active_generation],
    }


# ---------------------------------------------------------------------------
# ASIOS — self-optimization kernel endpoints
# ---------------------------------------------------------------------------

class AsiosObserveRequest(BaseModel):
    phase: int
    reward: float
    ethics: float
    coherence: float
    energy: float
    risk: float


@app.get("/asios/state")
async def asios_state():
    return get_asios().get_state()


@app.post("/asios/observe")
async def asios_observe(req: AsiosObserveRequest):
    get_asios().observe(
        phase     = req.phase,
        reward    = req.reward,
        ethics    = req.ethics,
        coherence = req.coherence,
        energy    = req.energy,
        risk      = req.risk,
    )
    audit("asios_observe", f"phase={req.phase} reward={req.reward:.4f} ethics={req.ethics:.4f}")
    return {"status": "ok", "obs_count": get_asios().obs_count}


@app.post("/asios/adapt")
async def asios_adapt():
    asios = get_asios()
    asios.adapt()
    state = asios.get_state()
    audit("asios_adapt", f"lr={state['learning_rate']:.2e} autonomy={state['autonomy_level']}")
    return {
        "status": "ok",
        "params": state["params"],
        "drift_detected": state["drift_detected"],
        "autonomy_level": state["autonomy_level"],
    }


@app.post("/asios/session/end")
async def asios_end_session():
    summary = get_asios().end_session()
    audit("asios_session_end", f"mean={summary['session_mean']} sessions={summary['session_count']}")
    return summary


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "uptime": time.time()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")
