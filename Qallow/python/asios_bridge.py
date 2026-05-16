"""
ASIOS Python Bridge — Adaptive Self-Improvement and Optimization System

Runs inside the FastAPI server process.  Mirrors the C kernel logic in Python
so the full runtime (LMDB, REST API) can participate in the
optimization loop without requiring the C library to be compiled.

Algorithms:
  - Welford online mean/variance → drift detection
  - Observation-quartile gradient estimation → intra-session param update
  - Inter-session finite-difference gradient → cross-session param refinement
  - EWMA confidence + stable-session counter → autonomy escalation
  - TF-IDF-inspired symbol utility → goal orientation vector
"""

import json
import logging
import math
import random
import struct
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

import lmdb

logger = logging.getLogger("ASIOS")

# ── Constants ──────────────────────────────────────────────────────────

ASIOS_VERSION = "1.0.0"
HISTORY_LEN   = 256
SYMBOL_SLOTS  = 64
SESSION_LOG   = 32

PARAM_EPSILON       = 0
PARAM_COUPLING      = 1
PARAM_COH_TARGET    = 2
PARAM_ENTROPY_FLOOR = 3
PARAM_NAMES    = ["epsilon", "coupling", "coherence_target", "entropy_floor"]
PARAM_DEFAULTS = [5e-6,  0.50, 0.981, 0.100]
PARAM_MIN      = [1e-7,  0.05, 0.950, 0.005]
PARAM_MAX      = [1e-4,  1.00, 0.9999, 0.500]

ETHICS_FLOOR      = 0.92
DRIFT_Z_THRESH    = 1.5
CONF_ALPHA        = 0.05
AUTONOMY_SESSIONS = 5
DEESCALATE_DRIFT  = 2
LR_INIT           = 0.01
LR_MIN            = 1e-5
LR_DECAY          = 0.995


class AsiosBridge:
    """
    Self-optimization kernel.  Instantiated once per server process and
    persisted across sessions via LMDB.
    """

    def __init__(self, lmdb_path: Path) -> None:
        self.lmdb_path = Path(lmdb_path)
        self._init_state()
        self._load()

    # ── State initialization ─────────────────────────────────────────

    def _init_state(self) -> None:
        self.params      = list(PARAM_DEFAULTS)
        self.prev_params = list(PARAM_DEFAULTS)
        self.param_gradient = [0.0] * 4
        self.learning_rate  = LR_INIT

        self._history: deque = deque(maxlen=HISTORY_LEN)

        # Welford accumulators (reward)
        self.reward_mean  = 0.0
        self.reward_m2    = 0.0
        self.ethics_mean  = 0.0
        self.coherence_mean = 0.0
        self.obs_count    = 0

        # Drift
        self.drift_score    = 0.0
        self.drift_detected = False
        self.drift_events   = 0
        self.last_drift_at  = 0.0

        # Autonomy
        self.confidence      = 0.0
        self.autonomy_level  = 0
        self.stable_sessions = 0
        self.consecutive_good = 0



        # Session accumulators
        self.session_id           = str(uuid.uuid4())
        self.session_start        = time.time()
        self.session_iter         = 0
        self.session_best_reward  = 0.0
        self.session_reward_acc   = 0.0

        # Cross-session log
        self.session_rewards: deque = deque(maxlen=SESSION_LOG)
        self.session_count   = 0
        self.lifetime_best   = 0.0

        self.last_update = time.time()
        self.initialized = True

    # ── Core observation ─────────────────────────────────────────────

    def observe(self, phase: int, reward: float, ethics: float,
                coherence: float, energy: float, risk: float) -> None:
        obs = dict(reward=reward, ethics=ethics, coherence=coherence,
                   energy=energy, risk=risk, timestamp=time.time(), phase=phase)
        self._history.append(obs)

        # Welford update (reward)
        self.obs_count += 1
        delta = reward - self.reward_mean
        self.reward_mean += delta / self.obs_count
        self.reward_m2   += delta * (reward - self.reward_mean)

        # Running mean for ethics and coherence
        self.ethics_mean    += (ethics    - self.ethics_mean)    / self.obs_count
        self.coherence_mean += (coherence - self.coherence_mean) / self.obs_count

        # Session bookkeeping
        self.session_iter += 1
        self.session_reward_acc += reward
        if reward > self.session_best_reward:
            self.session_best_reward = reward

        # Confidence EWMA
        good = (ethics >= ETHICS_FLOOR) and \
               (coherence >= self.params[PARAM_COH_TARGET] * 0.99)
        self.confidence     = self.confidence * (1 - CONF_ALPHA) + (1.0 if good else 0.0) * CONF_ALPHA
        self.consecutive_good = (self.consecutive_good + 1) if good else 0
        self.last_update    = time.time()

    # ── Drift detection ──────────────────────────────────────────────

    def check_drift(self) -> bool:
        if self.obs_count < 30:
            self.drift_score = 0.0
            return False

        history = list(self._history)
        recent_mean = sum(o["reward"] for o in history[-20:]) / 20
        std = math.sqrt(self.reward_m2 / max(self.obs_count - 1, 1))
        z   = abs(recent_mean - self.reward_mean) / (std + 1e-9)

        self.drift_score = z
        if z > DRIFT_Z_THRESH:
            if not self.drift_detected:
                self.drift_detected = True
                self.drift_events  += 1
                self.last_drift_at  = time.time()
                logger.info(f"[ASIOS] Drift: z={z:.2f} events={self.drift_events}")
            return True

        self.drift_detected = False
        return False

    # ── Parameter adaptation ─────────────────────────────────────────

    def adapt(self) -> None:
        if self.obs_count < 20:
            return

        self.check_drift()
        if self.drift_detected:
            self.learning_rate = min(self.learning_rate * 2.0, 0.05)

        if len(self._history) < 20:
            return

        std    = math.sqrt(self.reward_m2 / max(self.obs_count - 1, 1))
        hi_thr = self.reward_mean + 0.5 * std
        lo_thr = self.reward_mean - 0.5 * std

        hi_e = hi_c = lo_e = lo_c = 0.0
        n_hi = n_lo = 0

        for o in self._history:
            if o["reward"] > hi_thr:
                hi_e += o["energy"]; hi_c += o["coherence"]; n_hi += 1
            elif o["reward"] < lo_thr:
                lo_e += o["energy"]; lo_c += o["coherence"]; n_lo += 1

        if n_hi > 0 and n_lo > 0:
            d_e = hi_e / n_hi - lo_e / n_lo
            d_c = hi_c / n_hi - lo_c / n_lo

            self.param_gradient[PARAM_EPSILON]       = d_e * 1e-6
            self.param_gradient[PARAM_COUPLING]      = d_c * 0.05
            self.param_gradient[PARAM_COH_TARGET]    = d_c * 0.002
            self.param_gradient[PARAM_ENTROPY_FLOOR] = 0.0

            for i in range(4):
                self.params[i] += self.learning_rate * self.param_gradient[i]
                self.params[i]  = max(PARAM_MIN[i], min(PARAM_MAX[i], self.params[i]))

        self.learning_rate = max(self.learning_rate * LR_DECAY, LR_MIN)
        self._update_autonomy()
        self.last_update = time.time()
        self._persist()

    # ── Session lifecycle ────────────────────────────────────────────

    def end_session(self) -> dict:
        session_mean = (
            self.session_reward_acc / self.session_iter
            if self.session_iter > 0 else 0.0
        )

        self.session_rewards.append(session_mean)
        self.session_count += 1
        if session_mean > self.lifetime_best:
            self.lifetime_best = session_mean

        # Stability gate for autonomy
        if session_mean >= ETHICS_FLOOR and not self.drift_detected:
            self.stable_sessions += 1
        else:
            self.stable_sessions = 0

        # Inter-session finite-difference gradient
        rewards_list = list(self.session_rewards)
        if len(rewards_list) >= 2:
            delta_r = session_mean - rewards_list[-2]
            for i in range(4):
                delta_p = self.params[i] - self.prev_params[i]
                if abs(delta_p) > 1e-12:
                    self.param_gradient[i] = delta_r / delta_p
                    self.params[i] += self.learning_rate * self.param_gradient[i]
                    self.params[i]  = max(PARAM_MIN[i], min(PARAM_MAX[i], self.params[i]))

        # Checkpoint params and add exploration noise
        self.prev_params = list(self.params)
        noise = self.learning_rate * 0.5
        self.params[PARAM_EPSILON]  *= 1.0 + (random.random() - 0.5) * noise * 0.1
        self.params[PARAM_COUPLING] += (random.random() - 0.5) * noise * 0.02
        for i in range(4):
            self.params[i] = max(PARAM_MIN[i], min(PARAM_MAX[i], self.params[i]))

        self.learning_rate = max(self.learning_rate * LR_DECAY, LR_MIN)

        # Reset session accumulators
        self.session_iter        = 0
        self.session_reward_acc  = 0.0
        self.session_best_reward = 0.0
        self.drift_detected      = False
        self.drift_events        = 0
        self.session_start       = time.time()
        self.session_id          = str(uuid.uuid4())

        self._update_autonomy()
        self.last_update = time.time()
        self._persist()

        summary = dict(
            session_mean   = round(session_mean, 4),
            session_count  = self.session_count,
            lifetime_best  = round(self.lifetime_best, 4),
            autonomy_level = self.autonomy_level,
            stable_sessions= self.stable_sessions,
            params         = {PARAM_NAMES[i]: round(self.params[i], 8) for i in range(4)},
        )
        logger.info(f"[ASIOS] Session ended: {summary}")
        return summary

    # ── State introspection ──────────────────────────────────────────

    def get_state(self) -> dict:
        return dict(
            version         = ASIOS_VERSION,
            initialized     = self.initialized,
            autonomy_level  = self.autonomy_level,
            confidence      = round(self.confidence, 4),
            stable_sessions = self.stable_sessions,
            session_count   = self.session_count,
            lifetime_best   = round(self.lifetime_best, 4),
            obs_count       = self.obs_count,
            reward_mean     = round(self.reward_mean, 4),
            ethics_mean     = round(self.ethics_mean, 4),
            coherence_mean  = round(self.coherence_mean, 4),
            drift_score     = round(self.drift_score, 4),
            drift_detected  = self.drift_detected,
            drift_events    = self.drift_events,
            learning_rate   = round(self.learning_rate, 8),
            params          = {PARAM_NAMES[i]: round(self.params[i], 8) for i in range(4)},
            param_gradient  = {PARAM_NAMES[i]: round(self.param_gradient[i], 8) for i in range(4)},
            session         = dict(
                id           = self.session_id,
                iter         = self.session_iter,
                best_reward  = round(self.session_best_reward, 4),
                mean_reward  = round(self.session_reward_acc / max(self.session_iter, 1), 4),
            ),
            trajectory      = [round(r, 4) for r in list(self.session_rewards)[-10:]],
            trend           = self._session_trend(),
            last_update     = round(self.last_update, 1),
        )

    def get_llm_context(self) -> str:
        """Compact single-line context for LLM system prompt injection."""
        return (
            f"[ASIOS: autonomy={self.autonomy_level}/5 "
            f"confidence={self.confidence:.2f} "
            f"trend={self._session_trend()} "
            f"drift={'ACTIVE' if self.drift_detected else 'clear'} "
            f"sessions={self.session_count} "
            f"lifetime_best={self.lifetime_best:.3f} "
            f"epsilon={self.params[PARAM_EPSILON]:.2e}]\n"
        )

    # ── Internal helpers ─────────────────────────────────────────────

    def _update_autonomy(self) -> None:
        if self.stable_sessions >= AUTONOMY_SESSIONS and self.autonomy_level < 5:
            self.autonomy_level  += 1
            self.stable_sessions  = 0
            logger.info(f"[ASIOS] Autonomy ESCALATED → {self.autonomy_level}")

        if self.drift_events >= DEESCALATE_DRIFT and self.autonomy_level > 0:
            self.autonomy_level -= 1
            self.drift_events    = 0
            logger.info(f"[ASIOS] Autonomy DE-ESCALATED → {self.autonomy_level}")

    def _session_trend(self) -> str:
        rewards = list(self.session_rewards)
        if len(rewards) < 3:
            return "unknown"
        recent  = sum(rewards[-3:]) / 3
        earlier = sum(rewards[:3])  / 3
        if recent > earlier + 0.02:
            return "improving"
        if recent < earlier - 0.02:
            return "degrading"
        return "stable"

    # ── Persistence ──────────────────────────────────────────────────

    def _persist(self) -> None:
        state = self.get_state()
        # Include full data not exposed in public get_state
        state["_params_raw"]            = self.params
        state["_prev_params"]           = self.prev_params
        state["_session_rewards_full"]  = list(self.session_rewards)
        state["_reward_m2"]             = self.reward_m2
        state["_obs_count_raw"]         = self.obs_count

        try:
            self.lmdb_path.mkdir(parents=True, exist_ok=True)
            env = lmdb.open(str(self.lmdb_path), max_dbs=1, map_size=10 * 1024 * 1024)
            db  = env.open_db(b"asios")
            with env.begin(db=db, write=True) as txn:
                txn.put(b"state", json.dumps(state).encode())
            env.close()
        except Exception as e:
            logger.warning(f"[ASIOS] Persist failed: {e}")

    def _load(self) -> None:
        try:
            if not self.lmdb_path.exists():
                return
            env = lmdb.open(str(self.lmdb_path), readonly=True, max_dbs=1)
            db  = env.open_db(b"asios")
            with env.begin(db=db) as txn:
                val = txn.get(b"state")
            env.close()
            if not val:
                return

            s = json.loads(val.decode())
            self.autonomy_level   = s.get("autonomy_level", 0)
            self.confidence       = s.get("confidence", 0.0)
            self.stable_sessions  = s.get("stable_sessions", 0)
            self.session_count    = s.get("session_count", 0)
            self.lifetime_best    = s.get("lifetime_best", 0.0)
            self.obs_count        = s.get("_obs_count_raw", 0)
            self.reward_mean      = s.get("reward_mean", 0.0)
            self.reward_m2        = s.get("_reward_m2", 0.0)
            self.ethics_mean      = s.get("ethics_mean", 0.0)
            self.coherence_mean   = s.get("coherence_mean", 0.0)
            self.drift_score      = s.get("drift_score", 0.0)
            self.learning_rate    = s.get("learning_rate", LR_INIT)

            raw = s.get("_params_raw")
            if raw and len(raw) == 4:
                self.params = [max(PARAM_MIN[i], min(PARAM_MAX[i], raw[i])) for i in range(4)]

            prev = s.get("_prev_params")
            if prev and len(prev) == 4:
                self.prev_params = prev

            sr = s.get("_session_rewards_full", [])
            self.session_rewards = deque(sr, maxlen=SESSION_LOG)

            logger.info(
                f"[ASIOS] Loaded: autonomy={self.autonomy_level} "
                f"sessions={self.session_count} best={self.lifetime_best:.4f}"
            )
        except Exception as e:
            logger.warning(f"[ASIOS] Load failed (starting fresh): {e}")
