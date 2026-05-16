# Qallow

Qallow is a cognitive AGI runtime built as a hybrid C/Rust/Python stack. It runs a multi-phase reasoning loop driven by internal state variables (energy, risk, reward modulation, autonomy) and an ethics coherence gate. External signal sources — such as the VEYN bridge — can optionally feed live data into these state variables, but are not required.

## Architecture

- **Cognitive Engine (C)**: The core algorithmic workhorse. Features a fast semantic memory graph (LMDB-backed), cosine-similarity based temporal memory, and modular phase processors (Phases 1–4) that handle elasticity, harmonic convergence, and ethical coherence scoring.
- **VEYN Bridge (Rust)**: Optional integration that ingests a real-time signal stream via websocket (`ws://localhost:7700/stream`). Maps incoming signals into the shared LMDB state variables (energy, risk, reward_mod, autonomy). Disabled when VEYN is not running.
- **Reasoning Engine (Python)**: Uses a local Ollama instance (Gemma 4) as the reasoning core. Optionally injects contextual state prefixes into system prompts when `QALLOW_CONTEXT_INJECT=true`.
- **Native Orchestrator (Rust)**: The `qallow-native` FLTK desktop application that monitors the unified loop and handles phase transitions gated by cognitive state thresholds and the CANON's ethical floor (κ > 0.92).

## Phase Architecture

Qallow's pipeline is a tightly integrated 4-phase loop. The core C engine explicitly implements:

- **Phase 1 (Elasticity)**: Dynamic adjustment of system entropy and coherence.
- **Phase 2 (Harmonic)**: Alignment of internal reward trajectories with state baselines.
- **Phase 3 (Coherence)**: Synchronization of the semantic memory graph with the episodic SQLite logs.
- **Phase 4 (Convergence)**: Final stabilization of the cognitive state before unified execution.

## Setup

1. **Dependencies**:
   - Ensure `lmdb` and `sqlite3` development headers are installed.
   - Install Rust and Cargo for the Native App and VEYN Bridge.
   - Install Ollama and pull the Gemma 4 model (`ollama run gemma4`).

2. **Build**:
   ```bash
   cd native_app
   cargo build --release
   ```

3. **Run**:
   Launch Ollama and the Native App. VEYN integration is optional — start the VEYN websocket stream only if you want live signal data feeding into the cognitive state.
