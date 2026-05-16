# CGX_QV Monorepo

Cognitive AGI runtime and unified signal processing stack — a multi-phase reasoning engine with optional real-time sensor integration, built in a single Rust workspace.

---

## Repos

| Directory | Crate(s) | Purpose |
|---|---|---|
| [`CGX_VEYN/`](CGX_VEYN/) | `veyn-core`, `veyn-adapters`, `veyn-schemas`, `veyn-plugins` | Signal bridge daemon — normalizes data from connected devices and external APIs, exposes REST/WebSocket API |
| [`Qallow/`](Qallow/) | `qallow-native`, `qallow-veyn-bridge` | Cognitive AGI runtime — multi-phase reasoning loop with optional live VEYN telemetry integration |

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
        AGI runtime
        (multi-phase
        reasoning)
```

VEYN is an optional data source. When connected, Qallow ingests its WebSocket stream to enrich its internal cognitive state (energy, risk, reward modulation). Qallow runs fully without VEYN using its internal state defaults.

---

## Workspace

```
CGX_QV/
├── Cargo.toml                        # unified workspace root
├── CGX_VEYN/
│   ├── veyn-core/                    # daemon binary + API server
│   ├── veyn-adapters/                # BLE, EEG-OSC, Health SDK, Mock adapters
│   ├── veyn-schemas/                 # shared VeynEvent types
│   └── veyn-plugins/                 # WASM plugin host
└── Qallow/
    ├── native_app/                   # FLTK desktop orchestrator
    ├── core/qallow-veyn-bridge/      # WebSocket → LMDB signal bridge (optional)
    ├── src/                          # C cognitive engine (memory graph, phases 1–4)
    └── python/                       # Ollama reasoning layer
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

---

## License

- **CGX_VEYN** — Elastic License 2.0 (ELv2) © XINGXERX / CGX
- **Qallow** — MIT
