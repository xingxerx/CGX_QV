"""CGX-QV MCP Server — exposes VEYN (sensory daemon) and Qallow (reasoning engine)
as structured MCP tools and resources.

Implements the Model Context Protocol open standard. Compatible with any MCP client:
Claude Desktop, Cursor, Zed, Continue.dev, VS Code MCP extensions, and custom agents.

Environment variables:
  VEYN_URL    Base URL for the VEYN daemon   (default: http://localhost:7700)
  VEYN_TOKEN  Bearer token for VEYN auth     (default: read from ~/.local/share/veyn/token)
  QALLOW_URL  Base URL for the Qallow server (default: http://localhost:5000)

Run:
  uv run cgx-qv-mcp          # stdio transport (default, works with all MCP clients)
  uv run cgx-qv-mcp --sse    # SSE transport for remote/HTTP clients
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

from veyn_sdk import VeynClient, ContextSnapshot, VeynEvent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VEYN_URL = os.getenv("VEYN_URL", "http://localhost:7700")
VEYN_TOKEN = os.getenv("VEYN_TOKEN")
QALLOW_URL = os.getenv("QALLOW_URL", "http://localhost:5000")

mcp = FastMCP("CGX-QV")

# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------

def _veyn() -> VeynClient:
    if VEYN_TOKEN:
        return VeynClient(VEYN_URL, token=VEYN_TOKEN)
    return VeynClient.from_token_file(VEYN_URL)


def _qallow_get(path: str, **params: Any) -> Any:
    with httpx.Client(base_url=QALLOW_URL, timeout=10.0) as c:
        r = c.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()


def _qallow_post(path: str, body: Optional[dict] = None) -> Any:
    with httpx.Client(base_url=QALLOW_URL, timeout=30.0) as c:
        r = c.post(path, json=body or {})
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# VEYN tools — sensory / biometric layer
# ---------------------------------------------------------------------------

@mcp.tool()
def veyn_health() -> dict:
    """Check VEYN daemon liveness. Returns uptime, event rate, and connected device count."""
    return _veyn().health()


@mcp.tool()
def veyn_recent_events(limit: int = 50) -> list[dict]:
    """Return the most recent raw sensor events from the VEYN ring buffer (up to ~1000).

    Args:
        limit: Maximum number of events to return (1–1000).
    """
    events = _veyn().recent_events()
    return [
        {"metric": e.metric, "value": e.value, "unit": e.unit,
         "ts": e.ts, "device_id": e.device_id, "source": e.source}
        for e in events[:max(1, min(limit, 1000))]
    ]


@mcp.tool()
def veyn_get_metric(metric: str) -> dict:
    """Return the latest value for a specific VEYN metric (e.g. heart_rate, hrv, spo2).

    Args:
        metric: Metric name as reported by the sensor adapter (e.g. 'heart_rate', 'hrv', 'eeg_beta').
    """
    e: VeynEvent = _veyn().metric(metric)
    return {"metric": e.metric, "value": e.value, "unit": e.unit,
            "ts": e.ts, "device_id": e.device_id, "source": e.source}


@mcp.tool()
def veyn_list_devices() -> list[dict]:
    """List all devices VEYN has seen, with connection state and last-seen timestamp."""
    return _veyn().devices()


@mcp.tool()
def veyn_get_presence() -> list[dict]:
    """Return presence state for each tracked device (present/absent + last_seen ms)."""
    return _veyn().presence()


@mcp.tool()
def veyn_context_current() -> dict:
    """Return the current synthesized context snapshot from VEYN.

    Includes detected intent, confidence score, active devices, and state deltas
    (recent metric changes). This is the primary 'what is the user doing right now'
    signal consumed by Qallow.
    """
    ctx: ContextSnapshot = _veyn().context_current()
    return {
        "timestamp_ms": ctx.timestamp_ms,
        "session_id": ctx.session_id,
        "intent": ctx.intent,
        "confidence": ctx.confidence,
        "active_devices": ctx.active_devices,
        "state_deltas": [
            {"device_id": d.device_id, "metric": d.metric,
             "value": d.value, "unit": d.unit, "ts": d.ts}
            for d in ctx.state_deltas
        ],
    }


@mcp.tool()
def veyn_context_history(n: int = 10) -> list[dict]:
    """Return the N most recent historical context snapshots from VEYN.

    Args:
        n: Number of snapshots to return (default 10, max ~100).
    """
    snapshots = _veyn().context_history(n=n)
    return [
        {
            "timestamp_ms": ctx.timestamp_ms,
            "session_id": ctx.session_id,
            "intent": ctx.intent,
            "confidence": ctx.confidence,
            "active_devices": ctx.active_devices,
            "state_deltas": [
                {"device_id": d.device_id, "metric": d.metric,
                 "value": d.value, "unit": d.unit, "ts": d.ts}
                for d in ctx.state_deltas
            ],
        }
        for ctx in snapshots
    ]


@mcp.tool()
def veyn_notify(title: str, body: str, target_device: Optional[str] = None) -> dict:
    """Send a notification (haptic alert or system notification) to a connected device.

    Args:
        title: Notification title (short, shown as headline).
        body: Notification body text.
        target_device: Optional device ID to target. If omitted, broadcasts to all devices.
    """
    return _veyn().notify(title=title, body=body, target_device=target_device)


@mcp.tool()
def veyn_list_plugins() -> list[dict]:
    """List loaded WASM device adapter plugins (Garmin, Whoop, custom adapters)."""
    return _veyn().plugins()


# ---------------------------------------------------------------------------
# Qallow tools — reasoning / cognition layer
# ---------------------------------------------------------------------------

@mcp.tool()
def qallow_metrics() -> dict:
    """Return Qallow's current cognitive metrics.

    Includes active phase, uptime, overlay_stability (energy), ethics_score,
    coherence, and the raw biometric values read from VEYN via LMDB.
    Use this to understand the current cognitive state before making decisions.
    """
    return _qallow_get("/metrics")


@mcp.tool()
def qallow_start_phase(phase: int, ticks: int = 100) -> dict:
    """Start a Qallow reasoning phase (1–5).

    Phases map to cognitive modes:
      1 — Elasticity   (high plasticity, wide exploration)
      2 — Harmonic     (balanced integration)
      3 — Coherence    (convergence toward stable attractor)
      4 — Convergence  (deep focus, low entropy)
      5 — Synthesis    (output generation)

    Starting a phase also seeds ASIOS with current biometric state.

    Args:
        phase: Phase number 1–5.
        ticks: Processor tick budget for this phase run (default 100).
    """
    if not 1 <= phase <= 5:
        raise ValueError("phase must be 1–5")
    return _qallow_post(f"/phase/{phase}/start", {"ticks": ticks})


@mcp.tool()
def qallow_stop_phase() -> dict:
    """Stop the currently running Qallow reasoning phase.

    Triggers an ASIOS adaptation step at phase exit, updating kernel parameters
    based on the session trajectory.
    """
    return _qallow_post("/phase/stop")


@mcp.tool()
def qallow_chat(message: str) -> str:
    """Send a message to the Qallow local LLM (Gemma 4 via Ollama).

    The LLM receives optional ASIOS context injection if QALLOW_CONTEXT_INJECT=true.
    Returns the model's reply as a string.

    Args:
        message: The prompt or message to send to the local model.
    """
    result = _qallow_post("/chat", {"message": message})
    return result.get("reply", "")


@mcp.tool()
def qallow_get_logs(limit: int = 50) -> list[dict]:
    """Return recent Qallow audit log entries (phase starts/stops, chat calls, swarm events).

    Args:
        limit: Number of most recent entries to return (max 200).
    """
    logs = _qallow_get("/logs")
    return logs[-min(limit, 200):]


@mcp.tool()
def qallow_export(format: str = "json") -> Any:
    """Export a snapshot of current Qallow state.

    Args:
        format: 'json' (default) or 'csv'.
    """
    return _qallow_get("/export", format=format)


# ---------------------------------------------------------------------------
# Qallow swarm tools — evolutionary ethics weight search
# ---------------------------------------------------------------------------

@mcp.tool()
def qallow_swarm_spawn(n: int = 5, divergence_factor: float = 0.05) -> dict:
    """Spawn a new generation of swarm offspring from the current parent agent.

    Each offspring inherits the parent's ethics weights (safety, clarity, human)
    perturbed by divergence_factor. Use qallow_swarm_evolve to evaluate and
    select a survivor after spawning.

    Args:
        n: Number of offspring to spawn (1–32).
        divergence_factor: Perturbation magnitude for weight mutation (0–1, default 0.05).
    """
    return _qallow_post("/swarm/spawn", {"n": n, "divergence_factor": divergence_factor})


@mcp.tool()
def qallow_swarm_evolve(learning_rate: float = 0.1) -> dict:
    """Evaluate the active swarm generation and select a survivor.

    Fitness = ethics_coherence × reward_signal × calm_factor, all derived from
    current VEYN biometrics. The winner's weights are blended back into the
    parent via EMA. Clears the active generation.

    Args:
        learning_rate: EMA blend rate for feeding winner weights back to parent (0–1).
    """
    return _qallow_post("/swarm/evolve", {"learning_rate": learning_rate})


@mcp.tool()
def qallow_swarm_status() -> dict:
    """Return the current swarm state: parent weights, generation count, and active offspring."""
    return _qallow_get("/swarm/status")


# ---------------------------------------------------------------------------
# Qallow ASIOS tools — self-optimization kernel
# ---------------------------------------------------------------------------

@mcp.tool()
def qallow_asios_state() -> dict:
    """Return the full ASIOS kernel state.

    Includes trainable parameters (epsilon, coupling, coherence_target, entropy_floor),
    current autonomy level (0–5), drift detection status, learning rate,
    session count, and lifetime best reward.
    """
    return _qallow_get("/asios/state")


@mcp.tool()
def qallow_asios_observe(
    phase: int,
    reward: float,
    ethics: float,
    coherence: float,
    energy: float,
    risk: float,
) -> dict:
    """Feed an observation into the ASIOS self-optimization kernel.

    ASIOS accumulates observations within a session and uses them to estimate
    parameter gradients. Autonomy level escalates when sessions are consistently
    stable (drift-free, ethics above floor).

    Args:
        phase: Current reasoning phase (1–5).
        reward: Reward signal for this observation (0.0–1.0).
        ethics: Ethics score (0.0–1.0, higher = more aligned).
        coherence: Cognitive coherence score (0.0–1.0).
        energy: Energy/HRV-derived energy level (0.0–1.0).
        risk: Current risk estimate (0.0–1.0, lower = calmer).
    """
    return _qallow_post("/asios/observe", {
        "phase": phase, "reward": reward, "ethics": ethics,
        "coherence": coherence, "energy": energy, "risk": risk,
    })


@mcp.tool()
def qallow_asios_adapt() -> dict:
    """Trigger an ASIOS intra-session adaptation step.

    Estimates parameter gradients from accumulated observations this session
    and applies a bounded update. Returns updated params, drift status,
    and autonomy level.
    """
    return _qallow_post("/asios/adapt")


@mcp.tool()
def qallow_asios_end_session() -> dict:
    """End the current ASIOS session and apply inter-session parameter refinement.

    Computes session mean reward, runs finite-difference gradient estimation
    across sessions, updates autonomy gating, and persists state to LMDB.
    Returns session summary with mean reward and cumulative session count.
    """
    return _qallow_post("/asios/session/end")


# ---------------------------------------------------------------------------
# MCP Resources — browsable live state
# ---------------------------------------------------------------------------

@mcp.resource("veyn://context")
def resource_veyn_context() -> str:
    """Current synthesized VEYN context snapshot (intent, confidence, active devices, deltas)."""
    try:
        ctx = veyn_context_current()
        return json.dumps(ctx, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.resource("veyn://devices")
def resource_veyn_devices() -> str:
    """All VEYN-tracked devices with connection state."""
    try:
        return json.dumps(_veyn().devices(), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.resource("qallow://metrics")
def resource_qallow_metrics() -> str:
    """Live Qallow cognitive metrics: phase, ethics_score, coherence, biometrics."""
    try:
        return json.dumps(_qallow_get("/metrics"), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.resource("qallow://logs")
def resource_qallow_logs() -> str:
    """Recent Qallow audit log (last 100 entries)."""
    try:
        logs = _qallow_get("/logs")
        return json.dumps(logs[-100:], indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.resource("qallow://swarm")
def resource_qallow_swarm() -> str:
    """Current swarm state: parent weights, generation, active offspring."""
    try:
        return json.dumps(_qallow_get("/swarm/status"), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.resource("qallow://asios")
def resource_qallow_asios() -> str:
    """ASIOS kernel state: params, autonomy level, drift status, session history."""
    try:
        return json.dumps(_qallow_get("/asios/state"), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
