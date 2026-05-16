/**
 * ASIOS — Adaptive Self-Improvement and Optimization System
 *
 * The self-optimization kernel for Qallow. Records phase observations,
 * learns optimal parameters via inter-session gradient estimation,
 * detects distributional drift, manages autonomy escalation, and
 * integrates external context into goal orientation.
 *
 * Usage from phase code:
 *   asios_state_t asios;
 *   asios_init(&asios);
 *   // after each phase exit:
 *   asios_observe(&asios, phase_n, reward, ethics, coherence, energy, risk);
 *   asios_adapt(&asios);
 *   float eps = asios_get_param(&asios, ASIOS_PARAM_EPSILON);
 *   asios_save(&asios, "asios_state.json");
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <time.h>

#define ASIOS_VERSION             "1.0.0"
#define ASIOS_HISTORY_LEN         256
#define ASIOS_SESSION_LOG_LEN     32
#define ASIOS_PARAM_COUNT         4

/* Parameter indices */
#define ASIOS_PARAM_EPSILON       0   /* Phase exploration rate      default: 5e-6  */
#define ASIOS_PARAM_COUPLING      1   /* Oscillator coupling const   default: 0.5   */
#define ASIOS_PARAM_COH_TARGET    2   /* Adaptive coherence target   default: 0.981 */
#define ASIOS_PARAM_ENTROPY_FLOOR 3   /* Minimum entropy allowance   default: 0.1   */

/* Operational thresholds */
#define ASIOS_ETHICS_FLOOR        0.92f
#define ASIOS_DRIFT_Z_THRESH      1.5f   /* z-score triggering drift alert          */
#define ASIOS_CONF_ALPHA          0.05f  /* EWMA smoothing for confidence score      */
#define ASIOS_AUTONOMY_SESSIONS   5      /* Consecutive stable sessions to escalate  */
#define ASIOS_DEESCALATE_DRIFT    2      /* Drift events to trigger de-escalation    */
#define ASIOS_LR_INIT             0.01f
#define ASIOS_LR_MIN              1e-5f
#define ASIOS_LR_DECAY            0.995f

/* One observation recorded at a phase exit */
typedef struct {
    float   reward;
    float   ethics;      /* normalized [0,1] */
    float   coherence;
    float   energy;
    float   risk;
    time_t  timestamp;
    int     phase;       /* 1–4 */
} asios_observation_t;

/* Full kernel state — persist across sessions via asios_save/load */
typedef struct {
    /* Learned parameters (current session) */
    float   params[ASIOS_PARAM_COUNT];
    float   prev_params[ASIOS_PARAM_COUNT]; /* reference params from last session */

    /* Per-param gradient estimates */
    float   param_gradient[ASIOS_PARAM_COUNT];
    float   learning_rate;

    /* Observation ring buffer */
    asios_observation_t history[ASIOS_HISTORY_LEN];
    int     history_head;
    int     history_len;

    /* Global Welford statistics (reward variance drives drift detection) */
    double  reward_mean;
    double  reward_m2;
    double  ethics_mean;
    double  coherence_mean;
    uint64_t obs_count;

    /* Drift detection */
    float   drift_score;          /* z-score of recent vs global reward mean */
    bool    drift_detected;
    int     drift_events;
    time_t  last_drift_at;

    /* Autonomy control */
    float   confidence;           /* EWMA of (ethics_ok AND coherence_ok) */
    int     autonomy_level;       /* 0=supervised … 5=fully autonomous */
    int     stable_sessions;      /* consecutive sessions above floor */
    int     consecutive_good;     /* consecutive observations above floor */

    /* Current session accumulators */
    uint64_t session_id;
    time_t  session_start;
    int     session_iter;
    float   session_best_reward;
    float   session_reward_acc;

    /* Cross-session ring log */
    float   session_rewards[ASIOS_SESSION_LOG_LEN];
    int     session_rewards_head;
    int     session_count;
    float   lifetime_best;

    /* Meta */
    char    version[16];
    time_t  last_update;
    bool    initialized;
} asios_state_t;

/* ── Public API ─────────────────────────────────────────────────────── */

/** Initialize with defaults; call before first observe(). */
void  asios_init(asios_state_t *s);

/** Record one observation at a phase exit. */
void  asios_observe(asios_state_t *s, int phase,
                    float reward, float ethics,
                    float coherence, float energy, float risk);

/** Run one adaptation step: gradient estimation, param update, drift check, autonomy. */
void  asios_adapt(asios_state_t *s);

/** Drift-only check; returns true when drift is active. */
bool  asios_check_drift(asios_state_t *s);

/** Re-evaluate and possibly change autonomy level; returns new level. */
int   asios_update_autonomy(asios_state_t *s);

/** End-of-session accounting: inter-session gradient, session log, reset accumulators. */
void  asios_end_session(asios_state_t *s);

/** Return current learned parameter value. param_idx = ASIOS_PARAM_* constant. */
float asios_get_param(const asios_state_t *s, int param_idx);

/** Serialize state to JSON. Returns malloc'd string; caller must free(). */
char *asios_to_json(const asios_state_t *s);

/** Deserialize state from JSON (tolerates missing fields). */
void  asios_from_json(asios_state_t *s, const char *json);

/** Save state to file. Returns 0 on success, -1 on error. */
int   asios_save(const asios_state_t *s, const char *path);

/** Load state from file. Returns 0 on success, -1 if file missing. */
int   asios_load(asios_state_t *s, const char *path);
