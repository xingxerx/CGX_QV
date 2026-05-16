use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        Path, Query, State,
    },
    http::{header, StatusCode},
    response::{Html, IntoResponse, Json, Response, Sse},
    routing::{get, post},
    Router,
};
use axum::response::sse::{Event, KeepAlive};
use prometheus_client::encoding::text::encode;
use serde::Deserialize;
use serde_json::json;
use std::convert::Infallible;
use std::sync::atomic::Ordering;
use tokio::sync::broadcast::error::RecvError;
use tokio::time::{interval, Duration};
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt as _;
use tracing::warn;
use veyn_schemas::{ContextSnapshot, StateDelta, VeynEvent, VeynNotification};

use super::state::AppState;

const DASHBOARD: &str = include_str!("dashboard.html");

pub fn router(state: AppState) -> Router {
    // Legacy unversioned routes kept for backward compat.
    let legacy = Router::new()
        .route("/", get(dashboard))
        .route("/health", get(health))
        .route("/events/recent", get(events_recent))
        .route("/metrics/:metric", get(metrics_get))
        .route("/devices", get(devices_list))
        .route("/plugins", get(plugins_list))
        .route("/stream", get(ws_stream))
        .route("/notify", post(notify_post))
        .route("/presence", get(presence_get))
        .route("/gestures/recent", get(gestures_recent));

    // Versioned /v1/ routes.
    let v1 = Router::new()
        .route("/v1/health", get(health))
        .route("/v1/events/recent", get(events_recent))
        .route("/v1/metrics/:metric", get(metrics_get))
        .route("/v1/metrics/prometheus", get(prometheus_metrics))
        .route("/v1/devices", get(devices_list))
        .route("/v1/plugins", get(plugins_list))
        .route("/v1/stream", get(ws_stream))
        .route("/v1/stream/sse", get(sse_stream))
        .route("/v1/notify", post(notify_post))
        .route("/v1/presence", get(presence_get))
        .route("/v1/gestures/recent", get(gestures_recent))
        .route("/v1/context/current", get(context_current))
        .route("/v1/context/history", get(context_history))
        .route("/v1/context/subscribe", get(context_subscribe));

    legacy.merge(v1).with_state(state)
}

// ── Dashboard ──────────────────────────────────────────────────────────────────

async fn dashboard() -> Html<&'static str> {
    Html(DASHBOARD)
}

// ── GET /health ────────────────────────────────────────────────────────────────

async fn health(State(state): State<AppState>) -> Json<serde_json::Value> {
    let uptime = state.start_time.elapsed().as_secs();
    let raw = state.raw_event_count.load(Ordering::Relaxed);
    let filtered = state.event_count.load(Ordering::Relaxed);
    let ratio = *state.compression_ratio.lock().unwrap();
    let connected_devices = state.devices.lock().unwrap().len();
    let event_rate_hz = filtered.checked_div(uptime).unwrap_or(0);

    Json(json!({
        "status":            "ok",
        "version":           env!("CARGO_PKG_VERSION"),
        "uptime_s":          uptime,
        "session_id":        *state.session_id,
        "events_total":      filtered,
        "events_raw":        raw,
        "event_rate_hz":     event_rate_hz,
        "compression_ratio": ratio,
        "connected_devices": connected_devices,
    }))
}

// ── GET /events/recent ────────────────────────────────────────────────────────

async fn events_recent(State(state): State<AppState>) -> Json<serde_json::Value> {
    let events: Vec<VeynEvent> = state
        .recent_events
        .lock()
        .unwrap()
        .iter()
        .cloned()
        .collect();
    let count = events.len();
    Json(json!({ "events": events, "count": count }))
}

// ── GET /metrics/:metric ──────────────────────────────────────────────────────

async fn metrics_get(
    State(state): State<AppState>,
    Path(metric): Path<String>,
) -> impl IntoResponse {
    let found = state.latest_metrics.lock().unwrap().get(&metric).cloned();

    match found {
        Some(e) => (
            StatusCode::OK,
            Json(json!({
                "metric":    e.metric,
                "value":     e.value,
                "unit":      e.unit,
                "ts":        e.ts,
                "device_id": e.device_id,
                "source":    e.source,
            })),
        )
            .into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "metric not found", "metric": metric })),
        )
            .into_response(),
    }
}

// ── GET /devices ──────────────────────────────────────────────────────────────

async fn devices_list(State(state): State<AppState>) -> Json<serde_json::Value> {
    let devices: Vec<_> = state.devices.lock().unwrap().values().cloned().collect();
    let count = devices.len();
    Json(json!({ "devices": devices, "count": count }))
}

// ── GET /plugins ──────────────────────────────────────────────────────────────

async fn plugins_list(State(state): State<AppState>) -> Json<serde_json::Value> {
    let plugins = state.plugins.lock().unwrap().clone();
    let count = plugins.len();
    Json(json!({ "plugins": plugins, "count": count }))
}

// ── GET /v1/context/current ───────────────────────────────────────────────────

async fn context_current(State(state): State<AppState>) -> impl IntoResponse {
    match state.latest_context.lock().unwrap().clone() {
        Some(snap) => (StatusCode::OK, Json(serde_json::to_value(snap).unwrap())).into_response(),
        None => {
            // Build a snapshot on the fly from latest metrics.
            let snap = build_snapshot_from_metrics(&state);
            (StatusCode::OK, Json(serde_json::to_value(snap).unwrap())).into_response()
        }
    }
}

// ── GET /v1/context/history?n=10 ─────────────────────────────────────────────

#[derive(Deserialize)]
struct HistoryParams {
    #[serde(default = "default_n")]
    n: usize,
}

fn default_n() -> usize {
    10
}

async fn context_history(
    State(state): State<AppState>,
    Query(params): Query<HistoryParams>,
) -> Json<serde_json::Value> {
    let history: Vec<ContextSnapshot> = state
        .context_history
        .lock()
        .unwrap()
        .iter()
        .rev()
        .take(params.n)
        .cloned()
        .collect();
    let count = history.len();
    Json(json!({ "history": history, "count": count }))
}

fn build_snapshot_from_metrics(state: &AppState) -> ContextSnapshot {
    let now = chrono::Utc::now().timestamp_millis();
    let metrics = state.latest_metrics.lock().unwrap();
    let devices: Vec<String> = state.devices.lock().unwrap().keys().cloned().collect();

    let state_map: std::collections::HashMap<String, f64> = metrics
        .values()
        .map(|e| (e.metric.clone(), e.value))
        .collect();

    let deltas: Vec<StateDelta> = metrics
        .values()
        .map(|e| StateDelta {
            device_id: e.device_id.clone(),
            metric: e.metric.clone(),
            value: e.value,
            unit: e.unit.clone(),
            ts: e.ts,
        })
        .collect();

    // Simple built-in intent rules used when no external rules.toml is present.
    let (intent, confidence) = synthesize_intent_builtin(&state_map);

    ContextSnapshot {
        timestamp_ms: now,
        session_id: (*state.session_id).clone(),
        intent,
        confidence,
        active_devices: devices,
        state_deltas: deltas,
    }
}

fn synthesize_intent_builtin(state: &std::collections::HashMap<String, f64>) -> (String, f64) {
    if let Some(&hr) = state.get("heart_rate") {
        if hr > 100.0 {
            return ("user under physical stress".to_string(), 0.8);
        }
        if hr < 60.0 {
            if state.get("hrv").is_some_and(|&h| h > 50.0) {
                return ("user in calm/resting state".to_string(), 0.85);
            }
            return ("user in low-activity state".to_string(), 0.7);
        }
        return ("user in normal activity state".to_string(), 0.75);
    }
    ("observing".to_string(), 0.5)
}

// ── GET /stream  (WebSocket) ──────────────────────────────────────────────────

async fn ws_stream(
    ws: WebSocketUpgrade,
    State(state): State<AppState>,
    Query(params): Query<WsParams>,
) -> impl IntoResponse {
    let metric_filter: Vec<String> = params
        .metrics
        .as_deref()
        .unwrap_or("")
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect();
    let source_filter: Vec<String> = params
        .sources
        .as_deref()
        .unwrap_or("")
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect();
    ws.on_upgrade(move |socket| handle_socket(socket, state, metric_filter, source_filter))
}

async fn handle_socket(
    mut socket: WebSocket,
    state: AppState,
    metric_filter: Vec<String>,
    source_filter: Vec<String>,
) {
    let mut rx = state.broadcast_tx.subscribe();

    let passes_filter = |event: &VeynEvent| -> bool {
        let ok_metric = metric_filter.is_empty()
            || metric_filter.iter().any(|m| m == &event.metric);
        let ok_source = source_filter.is_empty()
            || source_filter.iter().any(|s| s == &event.source);
        ok_metric && ok_source
    };

    // Replay ring buffer to newly connected client (filtered).
    let replay: Vec<VeynEvent> = state
        .recent_events
        .lock()
        .unwrap()
        .iter()
        .filter(|e| passes_filter(e))
        .cloned()
        .collect();

    for event in &replay {
        let Ok(json) = serde_json::to_string(event) else {
            continue;
        };
        if socket.send(Message::Text(json)).await.is_err() {
            return;
        }
    }

    // Keepalive ping every 30 s.
    let mut ping_ticker = interval(Duration::from_secs(30));
    ping_ticker.tick().await; // consume the immediate first tick

    loop {
        tokio::select! {
            result = rx.recv() => {
                match result {
                    Ok(event) => {
                        if !passes_filter(&event) {
                            continue;
                        }
                        let json = match serde_json::to_string(&event) {
                            Ok(j) => j,
                            Err(_) => continue,
                        };
                        if socket.send(Message::Text(json)).await.is_err() {
                            break;
                        }
                    }
                    Err(RecvError::Lagged(n)) => warn!("WebSocket subscriber lagged {} events", n),
                    Err(RecvError::Closed) => break,
                }
            }
            _ = ping_ticker.tick() => {
                if socket.send(Message::Ping(vec![])).await.is_err() {
                    break;
                }
            }
            msg = socket.recv() => {
                match msg {
                    Some(Ok(Message::Pong(_))) => {}
                    Some(Ok(_)) => {}
                    _ => break,
                }
            }
        }
    }
}

// ── Phase 5 routes ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct NotifyRequest {
    title: String,
    body: String,
    target_device: Option<String>,
}

async fn notify_post(
    State(state): State<AppState>,
    Json(req): Json<NotifyRequest>,
) -> impl IntoResponse {
    let mut notif = VeynNotification::new(req.title, req.body);
    if let Some(dev) = req.target_device {
        notif = notif.for_device(dev);
    }
    let id = notif.id.clone();
    let _ = state.notification_tx.send(notif);
    (
        StatusCode::ACCEPTED,
        Json(json!({ "id": id, "status": "queued" })),
    )
}

async fn presence_get(State(state): State<AppState>) -> Json<serde_json::Value> {
    let presence: Vec<_> = state.presence.lock().unwrap().values().cloned().collect();
    let count = presence.len();
    Json(json!({ "presence": presence, "count": count }))
}

async fn gestures_recent(State(state): State<AppState>) -> Json<serde_json::Value> {
    let gestures: Vec<VeynEvent> = state
        .recent_events
        .lock()
        .unwrap()
        .iter()
        .filter(|e| e.source == "companion" && e.metric.starts_with("gesture_"))
        .cloned()
        .collect();
    let count = gestures.len();
    Json(json!({ "gestures": gestures, "count": count }))
}

// ── GET /v1/metrics/prometheus ────────────────────────────────────────────────

async fn prometheus_metrics(State(state): State<AppState>) -> Response {
    let mut body: Vec<u8> = Vec::new();
    {
        let registry = state.prometheus_registry.lock().unwrap();
        if encode(&mut body, &registry).is_err() {
            return (StatusCode::INTERNAL_SERVER_ERROR, "encode error").into_response();
        }
    }
    let text = match String::from_utf8(body) {
        Ok(s) => s,
        Err(_) => return (StatusCode::INTERNAL_SERVER_ERROR, "utf8 error").into_response(),
    };
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "text/plain; version=0.0.4; charset=utf-8")],
        text,
    )
        .into_response()
}

// ── GET /v1/stream/sse  (Server-Sent Events) ──────────────────────────────────

#[derive(Deserialize, Default)]
struct SseParams {
    /// Comma-separated metric names to include (empty = all).
    #[serde(default)]
    metrics: Option<String>,
    /// Comma-separated source names to include (empty = all).
    #[serde(default)]
    sources: Option<String>,
}

async fn sse_stream(
    State(state): State<AppState>,
    Query(params): Query<SseParams>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let metric_filter: Vec<String> = params
        .metrics
        .as_deref()
        .unwrap_or("")
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect();

    let source_filter: Vec<String> = params
        .sources
        .as_deref()
        .unwrap_or("")
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect();

    let rx = state.broadcast_tx.subscribe();
    let stream = BroadcastStream::new(rx)
        .filter_map(move |result| {
            let mf = metric_filter.clone();
            let sf = source_filter.clone();
            match result {
                Ok(event) => {
                    let pass_metric = mf.is_empty() || mf.iter().any(|m| m == &event.metric);
                    let pass_source = sf.is_empty() || sf.iter().any(|s| s == &event.source);
                    if pass_metric && pass_source {
                        if let Ok(data) = serde_json::to_string(&event) {
                            return Some(Ok(Event::default().data(data)));
                        }
                    }
                    None
                }
                Err(_) => None,
            }
        });

    Sse::new(stream).keep_alive(KeepAlive::default())
}

// ── GET /v1/stream  (WebSocket) with subscribe filtering ─────────────────────

/// Query params for subscribe filtering on the WebSocket stream.
#[derive(Deserialize, Default)]
struct WsParams {
    /// Bearer token (alternative to Authorization header).
    #[serde(default)]
    token: Option<String>,
    /// Comma-separated metric names to include (empty = all).
    #[serde(default)]
    metrics: Option<String>,
    /// Comma-separated source names to include (empty = all).
    #[serde(default)]
    sources: Option<String>,
}

// ── GET /v1/context/subscribe  (declarative filter DSL) ──────────────────────

/// JSON body / query params for context subscription.
#[derive(Deserialize, Default)]
struct SubscribeParams {
    /// Intent labels to match (empty = all).
    #[serde(default)]
    intents: Option<String>,
    /// Minimum confidence threshold (0.0–1.0).
    #[serde(default)]
    min_confidence: Option<f64>,
    /// Context tier: "raw" | "filtered" | "semantic".
    #[serde(default)]
    tier: Option<String>,
}

async fn context_subscribe(
    State(state): State<AppState>,
    Query(params): Query<SubscribeParams>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let intent_filter: Vec<String> = params
        .intents
        .as_deref()
        .unwrap_or("")
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect();
    let min_confidence = params.min_confidence.unwrap_or(0.0);

    // For context/subscribe we build synthetic snapshots from the broadcast stream.
    let rx = state.broadcast_tx.subscribe();
    let state_clone = state.clone();
    let stream = BroadcastStream::new(rx).filter_map(move |result| {
        let state_inner = state_clone.clone();
        let if_ = intent_filter.clone();
        match result {
            Ok(_event) => {
                // On every event, emit a fresh context snapshot if it passes filters.
                let snap = build_snapshot_from_metrics(&state_inner);
                let pass_intent =
                    if_.is_empty() || if_.iter().any(|i| snap.intent.contains(i.as_str()));
                let pass_confidence = snap.confidence >= min_confidence;
                if pass_intent && pass_confidence {
                    if let Ok(data) = serde_json::to_string(&snap) {
                        return Some(Ok(Event::default().event("context").data(data)));
                    }
                }
                None
            }
            Err(_) => None,
        }
    });

    Sse::new(stream).keep_alive(KeepAlive::default())
}
