# CGX_QV Monorepo

A sovereign, local-first AGI runtime with a universal signal bus.
CGX_QV is a monorepo containing two tightly coupled systems: VEYN, a universal signal ingestion and normalization daemon, and Qallow, a stateful multi-phase cognitive reasoning engine backed by a local LLM. Together they form a complete, private, self-optimizing AI infrastructure stack that runs entirely on your hardware.
No cloud. No accounts. No data leaving your machine.

---

## Repos

| Directory | Crate(s) | Purpose |
|---|---|---|
| [`CGX_VEYN/`](CGX_VEYN/) | `veyn-core`, `veyn-adapters`, `veyn-schemas`, `veyn-plugins` | Signal bridge daemon — normalizes data from connected devices and external APIs, exposes REST/WebSocket API |
| [`Qallow/`](Qallow/) | `qallow-native`, `qallow-veyn-bridge` | Cognitive AGI runtime — multi-phase reasoning loop with optional live VEYN telemetry integration |
| [`mcp/`](mcp/) | `cgx-qv-mcp` (Python) | Universal MCP server — exposes VEYN + Qallow as structured tools and resources for any MCP-compatible AI host |

---

VEYN is a universal data bus — a typed, normalized event stream that accepts input from any source (APIs, hardware, files, network, custom adapters) and collapses it into a single schema any downstream system can consume. Signal sources are interchangeable and optional.
Qallow is not a chatbot wrapper. It is a structured cognitive runtime — a 4-phase C engine managing internal state continuity, a semantic memory graph with cosine-similarity retrieval, a swarm evolution layer, and a self-optimizing parameter kernel (ASIOS) that improves across sessions. The local LLM (Gemma 4 via Ollama) is the final reasoning surface, not the core.

---

## Workspace

CGX_QV/
├── Cargo.toml                          # unified Rust workspace root
├── CGX_VEYN/
│   ├── veyn-core/                      # daemon binary + Axum API server
│   ├── veyn-adapters/                  # signal adapters (BLE, OSC, TCP relay, MQTT, Mock)
│   ├── veyn-schemas/                   # shared VeynEvent / VeynDevice types
│   ├── veyn-plugins/                   # WASM plugin runtime (wasmtime)
│   └── sdk/python/                     # Python client SDK
├── Qallow/
│   ├── native_app/                     # FLTK desktop orchestrator (Rust)
│   ├── core/qallow-veyn-bridge/        # WebSocket → LMDB signal bridge (optional, Rust)
│   ├── src/                            # C cognitive engine (phases 1–4, memory graph)
│   └── python/                         # FastAPI server + Ollama + swarm + ASIOS
└── mcp/
    ├── server.py                       # MCP server — all tools + resources
    └── pyproject.toml                  # cgx-qv-mcp package

---

## Quick Start

### Run VEYN (mock mode — no hardware needed)

```bash
VEYN_MOCK=true cargo run -p veyn-core
# API available at http://localhost:7700
curl http://localhost:7700/health
curl http://localhost:7700/events/recent
```

### Run Qallow

Requires VEYN running and Ollama with Gemma 4 pulled (`ollama run gemma4`).

```bash
cargo run -p qallow-native --release
```

---

## Stack

| Layer | Technology |
|---|---|
| Core language | Rust (stable) |
| Async runtime | Tokio |
| API server | Axum |
| Desktop UI | FLTK |
| Bluetooth LE | btleplug |
| EEG input | OSC/UDP (Mind Monitor → Muse S) |
| State store | LMDB |
| Session history | SQLite |
| Plugin runtime | WASM (wasmtime) |
| AI reasoning | Ollama (Gemma 4) |
| Cognitive engine | C |

### Run the MCP server

Requires VEYN and Qallow already running. Install with [uv](https://github.com/astral-sh/uv):

```bash
cd mcp
uv sync
uv run cgx-qv-mcp          # stdio — paste this path into your MCP client config
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "cgx-qv": {
      "command": "uv",
      "args": ["--directory", "/path/to/CGX_QV/mcp", "run", "cgx-qv-mcp"]
    }
  }
}
```

**Cursor / Zed / Continue / VS Code** — point your MCP config at the same `uv run` command above. All MCP-compatible hosts use the same stdio transport.

**Available tools** (20 total):

| Prefix | Tools |
|---|---|
| `veyn_` | `health`, `recent_events`, `get_metric`, `list_devices`, `get_presence`, `context_current`, `context_history`, `notify`, `list_plugins` |
| `qallow_` | `metrics`, `start_phase`, `stop_phase`, `chat`, `get_logs`, `export`, `swarm_spawn`, `swarm_evolve`, `swarm_status`, `asios_state`, `asios_observe`, `asios_adapt`, `asios_end_session` |

**Available resources**: `veyn://context`, `veyn://devices`, `qallow://metrics`, `qallow://logs`, `qallow://swarm`, `qallow://asios`

**Environment overrides**:
```bash
VEYN_URL=http://localhost:7700 QALLOW_URL=http://localhost:5000 uv run cgx-qv-mcp
```

---

## License

- **CGX_VEYN** — Elastic License 2.0 (ELv2) © XINGXERX / CGX
- **Qallow** — MIT
