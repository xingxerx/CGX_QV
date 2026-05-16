# CGX_QV Monorepo

Cognitive AGI runtime and unified signal processing stack — a multi-phase reasoning engine with optional real-time sensor integration, built in a single Rust workspace.

---

## Repos

| Directory | Crate(s) | Purpose |
|---|---|---|
| [`CGX_VEYN/`](CGX_VEYN/) | `veyn-core`, `veyn-adapters`, `veyn-schemas`, `veyn-plugins` | Signal bridge daemon — normalizes data from connected devices and external APIs, exposes REST/WebSocket API |
| [`Qallow/`](Qallow/) | `qallow-native`, `qallow-veyn-bridge` | Cognitive AGI runtime — multi-phase reasoning loop with optional live VEYN telemetry integration |
| [`mcp/`](mcp/) | `cgx-qv-mcp` (Python) | Universal MCP server — exposes VEYN + Qallow as structured tools and resources for any MCP-compatible AI host |

---

## How They Connect

```
Sensors / APIs / Wearables / Custom Sources
              │
              ▼
        VEYN Daemon  (localhost:7700)
        REST + WebSocket
              │  (optional)
              ▼
           Qallow
        AGI runtime          ◄──── MCP Server (stdio / SSE)
        (multi-phase                    │
        reasoning)              Any MCP-capable host
                              (Claude Desktop, Cursor,
                               Zed, Continue, VS Code,
                               custom agents)
```

VEYN is an optional data source. When connected, Qallow ingests its WebSocket stream to enrich its internal cognitive state (energy, risk, reward modulation). Qallow runs fully without VEYN using its internal state defaults.

The MCP server sits alongside both daemons and exposes their capabilities as structured tools — any MCP-compatible AI host can query sensor state, control reasoning phases, evolve the swarm, and tune the ASIOS kernel directly.

---

## Workspace

```
CGX_QV/
├── Cargo.toml                        # unified workspace root
├── CGX_VEYN/
│   ├── veyn-core/                    # daemon binary + API server
│   ├── veyn-adapters/                # BLE, EEG-OSC, Health SDK, Mock adapters
│   ├── veyn-schemas/                 # shared VeynEvent types
│   ├── veyn-plugins/                 # WASM plugin host
│   └── sdk/python/                   # Python client SDK (used by MCP server)
├── Qallow/
│   ├── native_app/                   # FLTK desktop orchestrator
│   ├── core/qallow-veyn-bridge/      # WebSocket → LMDB signal bridge (optional)
│   ├── src/                          # C cognitive engine (memory graph, phases 1–4)
│   └── python/                       # FastAPI server + Ollama + swarm + ASIOS
└── mcp/
    ├── server.py                     # MCP server (all tools + resources)
    └── pyproject.toml                # cgx-qv-mcp package
```

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
