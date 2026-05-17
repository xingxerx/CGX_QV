use std::collections::HashMap;

use chrono::Utc;
use tokio::io::AsyncWriteExt;
use tokio::sync::mpsc;
use tracing::{debug, error, info};
use veyn_schemas::{ContextSnapshot, StateDelta, VeynEvent};

use crate::api::state::{AdapterLabel, AppState};
use crate::compression::CompressionEngine;

fn dated_path(base: &str) -> String {
    let date = Utc::now().format("%Y-%m-%d");
    if let Some(dot) = base.rfind('.') {
        format!("{}-{}{}", &base[..dot], date, &base[dot..])
    } else {
        format!("{}-{}", base, date)
    }
}

async fn open_log(path: &str) -> Option<tokio::fs::File> {
    match tokio::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .await
    {
        Ok(f) => Some(f),
        Err(e) => {
            error!("cannot open JSONL log {}: {}", path, e);
            None
        }
    }
}

pub async fn run(mut rx: mpsc::Receiver<VeynEvent>, state: AppState, jsonl_base: String) {
    let mut current_date = Utc::now().date_naive();
    let mut current_path = dated_path(&jsonl_base);

    let mut file = match open_log(&current_path).await {
        Some(f) => f,
        None => return,
    };

    let mut engine = CompressionEngine::new(
        state.config.rules_path.clone(),
        state.config.debounce_ms.clone(),
        state.config.epsilons.clone(),
    );

    info!("dispatcher started — JSONL log: {}", current_path);

    while let Some(event) = rx.recv().await {
        state
            .raw_event_count
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);

        state.prometheus.events_raw_total
            .get_or_create(&AdapterLabel { adapter: event.source.clone() })
            .inc();

        if !engine.should_emit(&event) {
            continue;
        }

        debug!(
            source = %event.source,
            device = %event.device_id,
            metric = %event.metric,
            value  = %event.value,
            unit   = %event.unit,
            "event"
        );

        // Rotate log file on date change.
        let today = Utc::now().date_naive();
        if today != current_date {
            current_date = today;
            current_path = dated_path(&jsonl_base);
            info!("rotating event log -> {}", current_path);
            match open_log(&current_path).await {
                Some(f) => file = f,
                None => return,
            }
        }

        if let Ok(mut line) = serde_json::to_string(&event) {
            line.push('\n');
            if let Err(e) = file.write_all(line.as_bytes()).await {
                error!("JSONL write error: {}", e);
            } else if let Err(e) = file.flush().await {
                error!("JSONL flush error: {}", e);
            }
        }

        state.ingest(event.clone());

        let metric_state: HashMap<String, f64> = state
            .latest_metrics
            .lock()
            .unwrap()
            .values()
            .map(|e| (e.metric.clone(), e.value))
            .collect();

        let (intent, confidence) = engine.synthesize(&metric_state);

        let active_devices: Vec<String> = state.devices.lock().unwrap().keys().cloned().collect();

        let deltas: Vec<StateDelta> = state
            .latest_metrics
            .lock()
            .unwrap()
            .values()
            .map(|e| StateDelta {
                device_id: e.device_id.clone(),
                metric: e.metric.clone(),
                value: e.value,
                unit: e.unit.clone(),
                ts: e.ts,
            })
            .collect();

        let snapshot = ContextSnapshot {
            timestamp_ms: chrono::Utc::now().timestamp_millis(),
            session_id: (*state.session_id).clone(),
            intent,
            confidence,
            active_devices,
            state_deltas: deltas,
        };

        state.update_context(snapshot);

        *state.compression_ratio.lock().unwrap() = engine.compression_ratio();
    }
}
