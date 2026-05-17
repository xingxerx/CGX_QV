# CGX_QV — Master TODO

> Unified backlog for the full monorepo: CGX_VEYN, Qallow, and the MCP layer.
> Priority tiers: 🔴 Blocks real utility → 🟡 Needed for correctness/completeness → 🟢 Expands capability

---

## 🔴 CRITICAL — Blocks Real Utility

### Qallow: Goals API Not Exposed

The C engine has a full goal system (`qallow_cognitive_state_add_goal`) with priority vectors and goal tracking, but it is completely invisible to the MCP layer and the FastAPI server.

- [ ] Add `POST /goals` endpoint to Qallow Python server — accept `goal_name`, `goal_vector`, `priority`
- [ ] Add `GET /goals` endpoint — return current goal set with priorities and activation state
- [ ] Add `DELETE /goals/:name` — remove a goal by name
- [ ] Expose `qallow_goals_get`, `qallow_goals_set`, `qallow_goals_clear` as MCP tools
- [ ] Wire goal state into LLM context injection when `QALLOW_CONTEXT_INJECT=true`

**Why critical:** Without a goals API, the cognitive state operates without explicit objectives. The C engine supports it — it just isn't surfaced anywhere.

---

### Qallow: Swarm Fitness Function Is Hardcoded

Current fitness: `ethics_coherence × reward_signal × calm_factor`. This is domain-specific and non-configurable, making the swarm layer only useful for one narrow operating mode.

- [ ] Define a `FitnessConfig` schema — list of named signals with weights
- [ ] Accept fitness config on `POST /swarm/spawn` — override default at spawn time
- [ ] Persist fitness config per generation in swarm state
- [ ] Expose fitness config in `qallow_swarm_spawn` MCP tool parameters
- [ ] Document built-in fitness components and how to combine them

**Why critical:** The swarm is the most powerful part of the system. A hardcoded fitness function means it can only optimize toward one thing.

---

### Qallow: Autonomy Levels Are Undefined Above 0

ASIOS escalates autonomy from 0→5 based on stable sessions, but levels 1–5 have no defined behavioral difference. The escalation logic fires but nothing actually changes in the runtime.

- [ ] Define concrete behavioral contracts for each autonomy level:
  - Level 0: Fully supervised — no unsolicited action
  - Level 1: Can suggest next phase without being asked
  - Level 2: Can self-transition phases within a session
  - Level 3: Can spawn and evolve swarm without human trigger
  - Level 4: Can invoke `qallow_chat` autonomously on state change
  - Level 5: Full autonomous loop — observe → reason → act without human in loop
- [ ] Implement gating logic in phase runner and Qallow server for each level
- [ ] Expose current autonomy level and its behavioral contract in `/metrics` response
- [ ] Add autonomy ceiling config — operator-set max level (`QALLOW_MAX_AUTONOMY`)

**Why critical:** Autonomy escalation is ASIOS's primary output signal. If it doesn't do anything, the self-optimization kernel has no real effect on behavior.

---

### VEYN: WASM Plugin Device Proxy Layer Missing

WASM is sandboxed — plugins cannot touch host hardware directly. The plugin system is architecturally incomplete without a host-side device proxy.

- [ ] Define Device Proxy ABI — plugins register device descriptors (VID/PID, BLE UUID, serial pattern)
- [ ] Core daemon handles actual OS-level device open and passes byte buffers into WASM sandbox
- [ ] Add WASI host functions: `veyn_device_read`, `veyn_device_write`, `veyn_device_enumerate`
- [ ] Add plugin signature verification — WASM binary must be signed; core validates on load
- [ ] Update plugin SDK with device proxy examples

**Why critical:** Every WASM plugin that needs real hardware is broken without this.

---

### VEYN: `rules.toml` Semantic Compression Is Undocumented

The compression layer (debounce, epsilon filtering, context snapshot generation) is driven by `rules.toml` but the format and capabilities are completely undocumented. Nobody can configure it without reading source.

- [ ] Document the full `rules.toml` DSL — all available rule types, fields, and examples
- [ ] Add a `rules.toml.example` with annotated entries covering every rule type
- [ ] Add validation on daemon startup — reject malformed rules with clear error messages
- [ ] Expose current active rules via `GET /v1/compression/rules` endpoint

---

## 🟡 HIGH — Needed for Correctness / Completeness

### Qallow: Multi-Instance State Coordination

Multiple Qallow instances (or multiple AI hosts driving the same instance) currently have no coordination mechanism. Concurrent phase starts will conflict silently.

- [ ] Add session locking — only one active phase at a time, return 409 on conflict
- [ ] Add instance ID to all API responses — makes multi-host debugging tractable
- [ ] Design and document a basic coordination protocol for multi-agent scenarios
- [ ] Add `GET /session` endpoint — who owns the current session, started when, what phase

---

### Qallow: ASIOS State Persistence to LMDB Is Not Verified

ASIOS claims to persist to LMDB at session end but there is no test or verification that parameters actually survive a process restart.

- [ ] Add integration test: run session → end session → restart server → verify ASIOS params match
- [ ] Add `/asios/state` response field `persisted_at` — timestamp of last successful write
- [ ] Add startup log line confirming ASIOS loaded from persisted state vs. initialized fresh

---

### Qallow: Phase Runners 16–20 Are Stubs (remove the 16-20 phase stubs stick with the 4 phases we'll add anymore if needed)

`phase_runners.c` implements phases 16–20 (Constraint Validation, State Persistence, Distributed Execution, Recursive Self-Audit, Result Synthesis) as `execv` calls to external binaries that don't exist.

- [ ] Decide: implement phases 16–20 natively in C or remove stubs and document the phase ceiling as 5
- [ ] If implementing: define the algorithmic contract for each phase matching the pattern of phases 1–4
- [ ] If removing: clean up `phase_runners.c` and update all documentation to reflect 4-phase architecture
- [ ] Either way: MCP `qallow_start_phase` currently accepts 1–5 — align with actual implementation

---

### VEYN: Platform Adapter Coverage

Core signal adapters are missing on Windows (primary development platform).

- [ ] Windows: `RawInput` / `WinUSB` HID adapter
- [ ] Windows: MIDI adapter (`midir` crate) — CC events, note on/off, clock
- [ ] Windows: Serial/UART adapter (`serialport` crate)
- [ ] Linux: `evdev` adapter — keyboard, mouse, gamepad via `/dev/input/event*`
- [ ] Cross-platform: Filesystem watcher adapter (`notify` crate) — emit events on file create/modify/delete
- [ ] Cross-platform: Network presence adapter — emit events on LAN device appear/disappear

---

### MCP: Error Responses Are Opaque

When a tool call fails, the MCP server returns a raw exception string. This gives the calling AI host no structured way to understand or recover from failures.

- [ ] Define a structured error schema: `{ "error": string, "code": string, "retryable": bool }`
- [ ] Apply consistent error wrapping to all MCP tool handlers
- [ ] Add `retryable: true` for transient failures (timeout, connection refused) vs. `false` for logic errors
- [ ] Document error codes in MCP server README

---

### MCP: No Authentication on Qallow Endpoints

VEYN enforces Bearer token auth. Qallow's FastAPI server has no authentication layer — anyone on the local network can call `/phase/1/start` or `/swarm/spawn`.

- [ ] Add Bearer token middleware to Qallow FastAPI server
- [ ] Generate token at first launch, store at `~/.local/share/qallow/token` (mode `0o600`)
- [ ] Accept token via `QALLOW_TOKEN` env override
- [ ] Update MCP server to pass Qallow token in requests
- [ ] Document in setup guide

---

## 🟢 CAPABILITY EXPANSION

### Qallow: Agent-to-Agent Coordination Protocol

Currently there is no way for two Qallow instances or two MCP-connected AI hosts to share state or coordinate on shared tasks.

- [ ] Design a lightweight coordination protocol over the VEYN event bus — instances publish state snapshots as `VeynEvent` with `source: "qallow"`
- [ ] Add `GET /peers` endpoint — discover other Qallow instances on the bus
- [ ] Add shared goal synchronization — peers can subscribe to each other's goal state
- [ ] Document the multi-agent architecture pattern

---

### Qallow: Configurable LLM Backend

Currently hardcoded to Ollama. Swapping models requires config file edits and a restart.

- [ ] Abstract LLM backend behind an interface — `OllamaBackend`, `AnthropicBackend`, `OpenAICompatBackend`
- [ ] Add `POST /llm/switch` endpoint — hot-swap backend without restart
- [ ] Expose current backend model and latency stats in `/metrics`
- [ ] Add per-backend timeout and retry config

---

### VEYN: Plugin Registry

WASM plugins are loaded from a local directory. There is no discovery, versioning, or sharing mechanism.

- [ ] Design plugin registry format — manifest index with name, version, description, download URL, signature
- [ ] Add `veyn-core` registry fetch command — `veyn plugins install <name>`
- [ ] Add `veyn-core` registry publish command — for community adapter authors
- [ ] Add plugin sandboxing policy config — per-plugin capability grants

---

### VEYN: TypeScript / Node.js SDK

The Python SDK exists. TypeScript is the most common language for AI agent tooling.

- [ ] Implement `veyn-sdk-ts` — connect, auth, subscribe to context stream, typed `VeynEvent` interfaces
- [ ] Publish to npm
- [ ] Add to monorepo under `CGX_VEYN/sdk/typescript/`

---

### MCP: Streaming Tool Responses

All MCP tools currently return complete responses. High-latency tools (Gemma 4 inference, swarm evolution) block the calling host until complete.

- [ ] Add streaming support to `qallow_chat` — stream LLM tokens as they arrive
- [ ] Add progress events to `qallow_swarm_evolve` — emit offspring evaluation results incrementally
- [ ] Document streaming usage pattern for MCP hosts that support it

---

### Developer Experience

- [ ] Integration test suite — spin up VEYN in mock mode, run Qallow phases, assert ASIOS state changes, verify MCP tool responses
- [ ] VEYN web dashboard at `localhost:7700/ui` — live context feed, connected devices, event log tail (HTML/JS, no framework)
- [ ] `docker compose up` brings the full stack — VEYN + Qallow + MCP server + Ollama
- [ ] `cargo xtask check` — single command runs `cargo fmt`, `clippy --workspace -D warnings`, and the test suite
- [ ] Add `CONTRIBUTING.md` — coding standards, PR process, how to write a WASM adapter

---

## DONE (Reference)

- [x] VEYN daemon — full REST + WebSocket API
- [x] VEYN adapters — Mock, BLE, OSC/EEG, TCP relay, MQTT
- [x] VEYN WASM plugin runtime with Garmin and Whoop stubs
- [x] VEYN cross-device notifications + gesture forwarding
- [x] VEYN LMDB state + SQLite history + JSONL audit log
- [x] Qallow 4-phase C cognitive engine
- [x] Qallow ASIOS self-optimization kernel
- [x] Qallow swarm spawn/evolve/status
- [x] Qallow semantic memory graph (LMDB + cosine similarity)
- [x] Qallow VEYN bridge (WebSocket → LMDB state mapping)
- [x] Qallow native FLTK desktop orchestrator
- [x] Qallow FastAPI server + Ollama integration
- [x] MCP server — 20 tools, 6 resources, stdio + SSE transport
- [x] README rewrite — root, VEYN, Qallow
- [x] Gemma 4 26B Ollama bridge (timeout fix — 300s)
