use std::collections::{HashMap, VecDeque};
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc, Mutex,
};
use std::time::Instant;

use chrono::Utc;
use prometheus_client::{
    encoding::EncodeLabelSet,
    metrics::{counter::Counter, family::Family, gauge::Gauge},
    registry::Registry,
};
use serde::Serialize;
use tokio::sync::broadcast;
use veyn_schemas::{
    ContextSnapshot, DeviceState, PresenceInfo, VeynDevice, VeynEvent, VeynNotification,
};

use crate::auth::ScopedToken;
use crate::config::Config;

// ── Prometheus label types ────────────────────────────────────────────────────

#[derive(Clone, Debug, Hash, PartialEq, Eq, EncodeLabelSet)]
pub struct AdapterLabel {
    pub adapter: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct PluginInfo {
    pub name: String,
    pub version: String,
    pub description: String,
}

const RECENT_CAP: usize = 1_000;
const BROADCAST_CAP: usize = 256;
const NOTIF_CAP: usize = 64;

/// Shared Prometheus metrics exported at `/v1/metrics/prometheus`.
#[derive(Clone)]
pub struct PrometheusMetrics {
    pub events_total: Family<AdapterLabel, Counter>,
    pub events_raw_total: Family<AdapterLabel, Counter>,
    pub connected_devices: Gauge,
    pub auth_failures_total: Counter,
}

#[derive(Clone)]
pub struct AppState {
    pub recent_events: Arc<Mutex<VecDeque<VeynEvent>>>,
    pub latest_metrics: Arc<Mutex<HashMap<String, VeynEvent>>>,
    pub devices: Arc<Mutex<HashMap<String, VeynDevice>>>,
    pub broadcast_tx: broadcast::Sender<VeynEvent>,
    pub notification_tx: broadcast::Sender<VeynNotification>,
    pub presence: Arc<Mutex<HashMap<String, PresenceInfo>>>,
    pub start_time: Arc<Instant>,
    /// Filtered event count (after compression).
    pub event_count: Arc<AtomicU64>,
    /// Total raw event count (before compression).
    pub raw_event_count: Arc<AtomicU64>,
    pub plugins: Arc<Mutex<Vec<PluginInfo>>>,
    /// All valid tokens (primary full-access + any scoped extras).
    pub scoped_tokens: Arc<Vec<ScopedToken>>,
    pub config: Arc<Config>,
    pub session_id: Arc<String>,
    pub context_history: Arc<Mutex<VecDeque<ContextSnapshot>>>,
    pub latest_context: Arc<Mutex<Option<ContextSnapshot>>>,
    pub compression_ratio: Arc<Mutex<f64>>,
    pub prometheus: Arc<PrometheusMetrics>,
    pub prometheus_registry: Arc<Mutex<Registry>>,
}

impl AppState {
    pub fn new(primary_token: ScopedToken, config: Config) -> Self {
        let (broadcast_tx, _) = broadcast::channel(BROADCAST_CAP);
        let (notification_tx, _) = broadcast::channel(NOTIF_CAP);
        let session_id = uuid::Uuid::new_v4().to_string();
        let history_cap = config.context_history_size;
        let config = Arc::new(config);

        // Build Prometheus registry.
        let events_total: Family<AdapterLabel, Counter> = Family::default();
        let events_raw_total: Family<AdapterLabel, Counter> = Family::default();
        let connected_devices: Gauge = Gauge::default();
        let auth_failures_total: Counter = Counter::default();

        let mut registry = Registry::default();
        registry.register("veyn_events_total", "Filtered events ingested per adapter", events_total.clone());
        registry.register("veyn_events_raw_total", "Raw events seen per adapter (before compression)", events_raw_total.clone());
        registry.register("veyn_connected_devices", "Number of currently connected devices", connected_devices.clone());
        registry.register("veyn_auth_failures_total", "Number of authentication failures", auth_failures_total.clone());

        let prometheus = Arc::new(PrometheusMetrics {
            events_total,
            events_raw_total,
            connected_devices,
            auth_failures_total,
        });

        Self {
            recent_events: Arc::new(Mutex::new(VecDeque::with_capacity(RECENT_CAP))),
            latest_metrics: Arc::new(Mutex::new(HashMap::new())),
            devices: Arc::new(Mutex::new(HashMap::new())),
            broadcast_tx,
            notification_tx,
            presence: Arc::new(Mutex::new(HashMap::new())),
            start_time: Arc::new(Instant::now()),
            event_count: Arc::new(AtomicU64::new(0)),
            raw_event_count: Arc::new(AtomicU64::new(0)),
            plugins: Arc::new(Mutex::new(Vec::new())),
            scoped_tokens: Arc::new(vec![primary_token]),
            config,
            session_id: Arc::new(session_id),
            context_history: Arc::new(Mutex::new(VecDeque::with_capacity(history_cap))),
            latest_context: Arc::new(Mutex::new(None)),
            compression_ratio: Arc::new(Mutex::new(1.0)),
            prometheus,
            prometheus_registry: Arc::new(Mutex::new(registry)),
        }
    }

    pub fn register_plugin(&self, info: PluginInfo) {
        self.plugins.lock().unwrap().push(info);
    }

    /// Ingest a compression-filtered event into all state stores.
    pub fn ingest(&self, event: VeynEvent) {
        self.event_count.fetch_add(1, Ordering::Relaxed);

        // Prometheus: filtered event counter.
        self.prometheus.events_total
            .get_or_create(&AdapterLabel { adapter: event.source.clone() })
            .inc();

        let device_count;
        {
            let mut devices = self.devices.lock().unwrap();
            let entry = devices.entry(event.device_id.clone()).or_insert_with(|| {
                VeynDevice::new(&event.device_id, &event.device_id, &event.source)
            });
            entry.state = DeviceState::Connected;
            entry.last_seen = Utc::now().timestamp_millis();
            device_count = devices.len() as i64;
        }
        self.prometheus.connected_devices.set(device_count);

        self.latest_metrics
            .lock()
            .unwrap()
            .insert(event.metric.clone(), event.clone());

        {
            let mut recent = self.recent_events.lock().unwrap();
            if recent.len() >= RECENT_CAP {
                recent.pop_front();
            }
            recent.push_back(event.clone());
        }

        let _ = self.broadcast_tx.send(event);
    }

    /// Push a new context snapshot into the history ring buffer.
    pub fn update_context(&self, snapshot: ContextSnapshot) {
        let cap = self.config.context_history_size;
        let mut hist = self.context_history.lock().unwrap();
        if hist.len() >= cap {
            hist.pop_front();
        }
        hist.push_back(snapshot.clone());
        drop(hist);
        *self.latest_context.lock().unwrap() = Some(snapshot);
    }
}
