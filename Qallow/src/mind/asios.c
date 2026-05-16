/**
 * ASIOS — Adaptive Self-Improvement and Optimization System
 *
 * Self-optimization kernel implementation.
 *
 * Algorithms:
 *   - Observation engine: Welford online mean/variance, ring buffer
 *   - Drift detection: two-window z-score (recent 20 vs global)
 *   - Parameter adaptation: observation-quartile gradient estimation +
 *                           inter-session finite-difference gradient
 *   - Autonomy control: EWMA confidence × stable-session counter
 *   - Symbol integration: TF-IDF-inspired utility = mean_reward × log(1+freq)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <stdint.h>

#include "asios.h"

/* Logging — defer to Qallow macros when available */
#ifndef QALLOW_LOG_INFO
#  define QALLOW_LOG_INFO(fmt, ...)  fprintf(stderr, "[ASIOS] "  fmt "\n", ##__VA_ARGS__)
#  define QALLOW_LOG_DEBUG(fmt, ...) ((void)0)
#  define QALLOW_LOG_ERROR(fmt, ...) fprintf(stderr, "[ASIOS!] " fmt "\n", ##__VA_ARGS__)
#endif

/* Parameter bounds [min, max, default] */
static const float PARAM_MIN[ASIOS_PARAM_COUNT]     = { 1e-7f, 0.05f, 0.950f,  0.005f };
static const float PARAM_MAX[ASIOS_PARAM_COUNT]     = { 1e-4f, 1.00f, 0.9999f, 0.500f };
static const float PARAM_DEFAULT[ASIOS_PARAM_COUNT] = { 5e-6f, 0.50f, 0.981f,  0.100f };

/* ── Internal helpers ────────────────────────────────────────────────── */

static inline float clampf(float x, float lo, float hi) {
    return x < lo ? lo : (x > hi ? hi : x);
}

static void _clamp_params(asios_state_t *s) {
    for (int i = 0; i < ASIOS_PARAM_COUNT; i++) {
        s->params[i] = clampf(s->params[i], PARAM_MIN[i], PARAM_MAX[i]);
    }
}

/* Welford online update for (mean, M2) given new observation x and count n. */
static void _welford(double *mean, double *m2, uint64_t n, double x) {
    double delta  = x - *mean;
    *mean        += delta / (double)n;
    double delta2 = x - *mean;
    *m2          += delta * delta2;
}

static inline double _std(double m2, uint64_t n) {
    return (n > 1) ? sqrt(m2 / (double)(n - 1)) : 1e-9;
}

/* Mean reward of the most recent `window` observations (ring buffer aware). */
static float _recent_reward_mean(const asios_state_t *s, int window) {
    int n = (window < s->history_len) ? window : s->history_len;
    if (n == 0) return 0.0f;
    double sum = 0.0;
    for (int i = 0; i < n; i++) {
        int idx = (s->history_head - 1 - i + ASIOS_HISTORY_LEN) % ASIOS_HISTORY_LEN;
        sum += s->history[idx].reward;
    }
    return (float)(sum / n);
}

/* ── Public API ──────────────────────────────────────────────────────── */

void asios_init(asios_state_t *s) {
    memset(s, 0, sizeof(*s));
    strncpy(s->version, ASIOS_VERSION, sizeof(s->version) - 1);

    for (int i = 0; i < ASIOS_PARAM_COUNT; i++) {
        s->params[i]      = PARAM_DEFAULT[i];
        s->prev_params[i] = PARAM_DEFAULT[i];
    }

    s->learning_rate  = ASIOS_LR_INIT;
    s->autonomy_level = 0;
    s->confidence     = 0.0f;
    s->session_id     = (uint64_t)time(NULL);
    s->session_start  = time(NULL);
    s->last_update    = time(NULL);
    s->initialized    = true;

    QALLOW_LOG_INFO("ASIOS v%s initialized", ASIOS_VERSION);
}

void asios_observe(asios_state_t *s, int phase,
                   float reward, float ethics,
                   float coherence, float energy, float risk)
{
    if (!s->initialized) asios_init(s);

    /* Push to ring buffer */
    asios_observation_t *obs = &s->history[s->history_head];
    obs->reward    = reward;
    obs->ethics    = ethics;
    obs->coherence = coherence;
    obs->energy    = energy;
    obs->risk      = risk;
    obs->timestamp = time(NULL);
    obs->phase     = phase;

    s->history_head = (s->history_head + 1) % ASIOS_HISTORY_LEN;
    if (s->history_len < ASIOS_HISTORY_LEN) s->history_len++;

    /* Welford update for reward variance; simple running mean for rest */
    s->obs_count++;
    _welford(&s->reward_mean, &s->reward_m2, s->obs_count, (double)reward);
    s->ethics_mean    += ((double)ethics    - s->ethics_mean)    / (double)s->obs_count;
    s->coherence_mean += ((double)coherence - s->coherence_mean) / (double)s->obs_count;

    /* Session accumulators */
    s->session_iter++;
    s->session_reward_acc += reward;
    if (reward > s->session_best_reward) s->session_best_reward = reward;

    /* Confidence EWMA: 1 if both ethics and coherence are above floor, else 0 */
    bool good = (ethics >= ASIOS_ETHICS_FLOOR)
             && (coherence >= s->params[ASIOS_PARAM_COH_TARGET] * 0.99f);
    s->confidence = s->confidence * (1.0f - ASIOS_CONF_ALPHA)
                  + (good ? 1.0f : 0.0f) * ASIOS_CONF_ALPHA;
    s->consecutive_good = good ? s->consecutive_good + 1 : 0;

    s->last_update = time(NULL);

    QALLOW_LOG_DEBUG("observe phase=%d r=%.4f e=%.4f c=%.4f conf=%.3f",
                     phase, reward, ethics, coherence, s->confidence);
}

bool asios_check_drift(asios_state_t *s) {
    if (s->obs_count < 30) { s->drift_score = 0.0f; return false; }

    float  recent = _recent_reward_mean(s, 20);
    double std    = _std(s->reward_m2, s->obs_count);
    float  z      = fabsf(recent - (float)s->reward_mean) / (float)(std + 1e-9);

    s->drift_score = z;

    if (z > ASIOS_DRIFT_Z_THRESH) {
        if (!s->drift_detected) {
            s->drift_detected = true;
            s->drift_events++;
            s->last_drift_at = time(NULL);
            QALLOW_LOG_INFO("Drift detected: z=%.2f, total_events=%d", z, s->drift_events);
        }
        return true;
    }

    s->drift_detected = false;
    return false;
}

void asios_adapt(asios_state_t *s) {
    if (!s->initialized || s->obs_count < 20) return;

    asios_check_drift(s);

    /* Widen exploration on drift to escape the shifted distribution */
    if (s->drift_detected) {
        s->learning_rate = clampf(s->learning_rate * 2.0f, ASIOS_LR_MIN, 0.05f);
        QALLOW_LOG_INFO("Drift: widened exploration lr=%.6f", s->learning_rate);
    }

    /* Gradient estimation: split history into high/low reward quartiles and
     * measure which biometric context correlates with high reward.
     * High energy in high-reward obs → epsilon too low (raise it).
     * High coherence in high-reward obs → coupling too low (raise it).  */
    if (s->history_len >= 20) {
        double std      = _std(s->reward_m2, s->obs_count);
        float  hi_thr   = (float)s->reward_mean + 0.5f * (float)std;
        float  lo_thr   = (float)s->reward_mean - 0.5f * (float)std;

        double hi_energy = 0, hi_coh = 0;
        double lo_energy = 0, lo_coh = 0;
        int    n_hi = 0, n_lo = 0;

        for (int i = 0; i < s->history_len; i++) {
            asios_observation_t *o = &s->history[i];
            if (o->reward > hi_thr) {
                hi_energy += o->energy; hi_coh += o->coherence; n_hi++;
            } else if (o->reward < lo_thr) {
                lo_energy += o->energy; lo_coh += o->coherence; n_lo++;
            }
        }

        if (n_hi > 0 && n_lo > 0) {
            float d_energy = (float)(hi_energy / n_hi - lo_energy / n_lo);
            float d_coh    = (float)(hi_coh    / n_hi - lo_coh    / n_lo);

            s->param_gradient[ASIOS_PARAM_EPSILON]       = d_energy * 1e-6f;
            s->param_gradient[ASIOS_PARAM_COUPLING]      = d_coh    * 0.05f;
            s->param_gradient[ASIOS_PARAM_COH_TARGET]    = d_coh    * 0.002f;
            s->param_gradient[ASIOS_PARAM_ENTROPY_FLOOR] = 0.0f;

            for (int i = 0; i < ASIOS_PARAM_COUNT; i++) {
                s->params[i] += s->learning_rate * s->param_gradient[i];
            }
            _clamp_params(s);
        }
    }

    s->learning_rate = clampf(s->learning_rate * ASIOS_LR_DECAY, ASIOS_LR_MIN, 0.1f);
    asios_update_autonomy(s);
    s->last_update = time(NULL);
}

int asios_update_autonomy(asios_state_t *s) {
    /* Escalate when we've had enough consistently good sessions */
    if (s->stable_sessions >= ASIOS_AUTONOMY_SESSIONS && s->autonomy_level < 5) {
        s->autonomy_level++;
        s->stable_sessions = 0;
        QALLOW_LOG_INFO("Autonomy ESCALATED → %d (conf=%.3f)", s->autonomy_level, s->confidence);
    }

    /* De-escalate when drift accumulates (distribution has shifted, system is unreliable) */
    if (s->drift_events >= ASIOS_DEESCALATE_DRIFT && s->autonomy_level > 0) {
        s->autonomy_level--;
        s->drift_events = 0;
        QALLOW_LOG_INFO("Autonomy DE-ESCALATED → %d (drift reset)", s->autonomy_level);
    }

    return s->autonomy_level;
}

void asios_end_session(asios_state_t *s) {
    float session_mean = (s->session_iter > 0)
        ? s->session_reward_acc / (float)s->session_iter
        : 0.0f;

    /* Log in session ring buffer */
    int idx = s->session_rewards_head % ASIOS_SESSION_LOG_LEN;
    s->session_rewards[idx] = session_mean;
    s->session_rewards_head++;
    s->session_count++;

    if (session_mean > s->lifetime_best) s->lifetime_best = session_mean;

    /* Track stability for autonomy escalation */
    if (session_mean >= ASIOS_ETHICS_FLOOR && !s->drift_detected) {
        s->stable_sessions++;
    } else {
        s->stable_sessions = 0;
    }

    /* Inter-session finite-difference gradient:
     * If reward improved and param changed → gradient is positive (keep going).
     * If reward dropped and param changed → gradient is negative (reverse).   */
    if (s->session_count >= 2) {
        int prev_idx  = ((idx - 1) + ASIOS_SESSION_LOG_LEN) % ASIOS_SESSION_LOG_LEN;
        float delta_r = session_mean - s->session_rewards[prev_idx];

        for (int i = 0; i < ASIOS_PARAM_COUNT; i++) {
            float delta_p = s->params[i] - s->prev_params[i];
            if (fabsf(delta_p) > 1e-12f) {
                s->param_gradient[i] = delta_r / delta_p;
                s->params[i] += s->learning_rate * s->param_gradient[i];
            }
        }
        _clamp_params(s);
    }

    /* Copy current as reference, then nudge with decaying noise for next session */
    memcpy(s->prev_params, s->params, sizeof(float) * ASIOS_PARAM_COUNT);
    float noise = s->learning_rate * 0.5f;
    s->params[ASIOS_PARAM_EPSILON]  *= 1.0f + ((float)rand() / RAND_MAX - 0.5f) * noise * 0.1f;
    s->params[ASIOS_PARAM_COUPLING] += ((float)rand() / RAND_MAX - 0.5f) * noise * 0.02f;
    _clamp_params(s);

    s->learning_rate = clampf(s->learning_rate * ASIOS_LR_DECAY, ASIOS_LR_MIN, 0.1f);

    /* Reset session accumulators */
    s->session_iter        = 0;
    s->session_reward_acc  = 0.0f;
    s->session_best_reward = 0.0f;
    s->drift_detected      = false;
    s->drift_events        = 0;
    s->session_start       = time(NULL);
    s->session_id++;

    asios_update_autonomy(s);
    s->last_update = time(NULL);

    QALLOW_LOG_INFO("Session %llu ended: mean=%.4f lifetime_best=%.4f autonomy=%d stable=%d",
                    (unsigned long long)s->session_id - 1,
                    session_mean, s->lifetime_best,
                    s->autonomy_level, s->stable_sessions);
}

float asios_get_param(const asios_state_t *s, int param_idx) {
    if (param_idx < 0 || param_idx >= ASIOS_PARAM_COUNT) return 0.0f;
    return s->params[param_idx];
}

/* ── JSON serialization ──────────────────────────────────────────────── */

char *asios_to_json(const asios_state_t *s) {
    /* Conservative upper bound: state + symbol table */
    char *buf = (char *)malloc(16384);
    if (!buf) return NULL;
    int pos = 0;

#define W(fmt, ...) pos += snprintf(buf + pos, 16384 - pos, fmt, ##__VA_ARGS__)

    W("{\n");
    W("  \"version\": \"%s\",\n", s->version);
    W("  \"last_update\": %ld,\n", (long)s->last_update);
    W("  \"initialized\": %s,\n", s->initialized ? "true" : "false");
    W("  \"autonomy_level\": %d,\n", s->autonomy_level);
    W("  \"confidence\": %.6f,\n", s->confidence);
    W("  \"stable_sessions\": %d,\n", s->stable_sessions);
    W("  \"session_count\": %d,\n", s->session_count);
    W("  \"session_iter\": %d,\n", s->session_iter);
    W("  \"session_best_reward\": %.6f,\n", s->session_best_reward);
    W("  \"lifetime_best\": %.6f,\n", s->lifetime_best);
    W("  \"obs_count\": %llu,\n", (unsigned long long)s->obs_count);
    W("  \"reward_mean\": %.6f,\n", s->reward_mean);
    W("  \"ethics_mean\": %.6f,\n", s->ethics_mean);
    W("  \"coherence_mean\": %.6f,\n", s->coherence_mean);
    W("  \"drift_score\": %.6f,\n", s->drift_score);
    W("  \"drift_detected\": %s,\n", s->drift_detected ? "true" : "false");
    W("  \"drift_events\": %d,\n", s->drift_events);
    W("  \"learning_rate\": %.8f,\n", s->learning_rate);
    W("  \"params\": [%.8f, %.8f, %.8f, %.8f],\n",
      s->params[0], s->params[1], s->params[2], s->params[3]);
    W("  \"prev_params\": [%.8f, %.8f, %.8f, %.8f],\n",
      s->prev_params[0], s->prev_params[1], s->prev_params[2], s->prev_params[3]);
    W("  \"param_gradient\": [%.8f, %.8f, %.8f, %.8f],\n",
      s->param_gradient[0], s->param_gradient[1], s->param_gradient[2], s->param_gradient[3]);

    /* Last 8 session rewards */
    W("  \"session_rewards\": [");
    int n_sess = s->session_count < 8 ? s->session_count : 8;
    for (int i = 0; i < n_sess; i++) {
        int si = ((s->session_rewards_head - n_sess + i) + ASIOS_SESSION_LOG_LEN)
                 % ASIOS_SESSION_LOG_LEN;
        W("%.4f%s", s->session_rewards[si], i < n_sess - 1 ? ", " : "");
    }
    W("],\n");

    W("  ]\n}\n");

#undef W
    return buf;
}

void asios_from_json(asios_state_t *s, const char *json) {
    if (!s || !json) return;

/* Scan for "key": numeric_value */
#define PARSE_D(key, field) do { \
    const char *_p = strstr(json, "\"" key "\""); \
    if (_p) { _p = strchr(_p + strlen("\"" key "\""), ':'); \
              if (_p) { double _v; if (sscanf(_p + 1, " %lf", &_v) == 1) (field) = _v; } } \
} while (0)

#define PARSE_I(key, field) do { \
    const char *_p = strstr(json, "\"" key "\""); \
    if (_p) { _p = strchr(_p + strlen("\"" key "\""), ':'); \
              if (_p) { int _v; if (sscanf(_p + 1, " %d", &_v) == 1) (field) = _v; } } \
} while (0)

    PARSE_I("autonomy_level", s->autonomy_level);
    PARSE_D("confidence",     s->confidence);
    PARSE_I("stable_sessions",s->stable_sessions);
    PARSE_I("session_count",  s->session_count);
    PARSE_I("session_iter",   s->session_iter);
    PARSE_D("lifetime_best",  s->lifetime_best);
    PARSE_D("obs_count",      s->reward_mean);   /* placeholder: obs_count as double */
    PARSE_D("reward_mean",    s->reward_mean);
    PARSE_D("ethics_mean",    s->ethics_mean);
    PARSE_D("coherence_mean", s->coherence_mean);
    PARSE_D("drift_score",    s->drift_score);
    PARSE_D("learning_rate",  s->learning_rate);

    /* obs_count needs integer parse */
    const char *p = strstr(json, "\"obs_count\"");
    if (p) { p = strchr(p + 11, ':'); if (p) { long long v; if (sscanf(p+1, " %lld", &v) == 1) s->obs_count = (uint64_t)v; } }

    /* params array */
    p = strstr(json, "\"params\"");
    if (p) { p = strchr(p, '['); if (p) sscanf(p+1, "%f, %f, %f, %f",
        &s->params[0], &s->params[1], &s->params[2], &s->params[3]); }

    /* prev_params array */
    p = strstr(json, "\"prev_params\"");
    if (p) { p = strchr(p, '['); if (p) sscanf(p+1, "%f, %f, %f, %f",
        &s->prev_params[0], &s->prev_params[1], &s->prev_params[2], &s->prev_params[3]); }

    _clamp_params(s);
    s->initialized = true;

#undef PARSE_D
#undef PARSE_I
}

int asios_save(const asios_state_t *s, const char *path) {
    char *json = asios_to_json(s);
    if (!json) return -1;

    FILE *f = fopen(path, "w");
    if (!f) { free(json); return -1; }
    fputs(json, f);
    fclose(f);
    free(json);
    return 0;
}

int asios_load(asios_state_t *s, const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    rewind(f);

    char *buf = (char *)malloc(len + 1);
    if (!buf) { fclose(f); return -1; }
    fread(buf, 1, len, f);
    buf[len] = '\0';
    fclose(f);

    asios_from_json(s, buf);
    free(buf);

    QALLOW_LOG_INFO("Loaded from %s: autonomy=%d sessions=%d best=%.4f",
                    path, s->autonomy_level, s->session_count, s->lifetime_best);
    return 0;
}
