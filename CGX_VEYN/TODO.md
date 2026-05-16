# VEYN — Master TODO

> Roadmap to a perfect, runnable-out-of-the-box build.
> Priority: 

🔴 Critical (blocks launch) → 
🟡 High (needed for safety/correctness) → 
🟢 Nice-to-have

-----

## 2. 🔴 Critical: Security — Kill the Local Keylogger Attack Surface

An unauthenticated WS server on `:7700` exposing raw HID is spyware-grade risk.

- [ ] 🟢 Consider adding mutual TLS (mTLS) option for high-security deployments

-----

## 3. 🔴 Critical: WASM Plugin Architecture Fix

WASM is sandboxed — it cannot touch host hardware without WASI host functions.

- [ ] 🔴 Add a **Device Proxy Layer** — plugins register a device descriptor (VID/PID, BLE UUID, serial pattern); the core daemon handles actual OS-level device open and passes byte buffers into the WASM sandbox
- [ ] 🟡 Add **plugin signature verification** — each plugin WASM binary must be signed; core validates on load
- [ ] 🟢 Publish example plugin: `veyn-plugin-midi-launchpad` as reference implementation

-----

## 5. 🟡 High: `veyn-adapters` — Platform Coverage

- [ ] 🔴 Linux: `evdev` HID adapter — keyboard, mouse, gamepad via `/dev/input/event*`
- [ ] 🔴 Linux: `hidraw` adapter for raw USB HID
- [ ] 🟡 macOS: `IOKit`/`IOHIDManager` adapter
- [ ] 🟡 Windows: `WinUSB`/`RawInput` adapter
- [ ] 🟡 MIDI adapter (`midir` crate) — CC events, note on/off, clock
- [ ] 🟡 Serial/UART adapter (`serialport` crate) — configurable baud, parity, stop bits
- [ ] 🟡 Filesystem watcher adapter (`notify` crate) — emit events on file create/modify/delete for specified paths
- [ ] 🟢 OSC (Open Sound Control) input adapter — for DAW/VJ software integration
- [ ] 🟢 Audio level adapter — RMS/peak metering from default input device (via `cpal`)

-----

## 7. 🟡 High: SDK (`/sdk`)

- [ ] 🟡 Add TypeScript/Node.js SDK — connect, auth, subscribe to context stream, typed interfaces
- [ ] 🟢 Add Go SDK

-----

## 8. 🟢 Developer Experience

- [ ] Add integration test suite — spin up daemon in test mode, connect mock devices, assert context snapshots
- [ ] Add a minimal web UI (`localhost:7700/ui`) — live context feed, connected devices, log tail (HTML/JS, no framework)

-----

## 9. 🟢 Phase 7+ AI Integration Prep

- [ ] Design and document the **Agent Handshake Protocol** — how an AI agent authenticates, declares its capability needs, and subscribes to the right context tier
- [ ] Document recommended integration patterns for sovereign local Ollama agents and local function calling
