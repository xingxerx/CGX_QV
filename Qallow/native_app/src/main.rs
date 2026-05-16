mod backend;
mod models;

use backend::api_client::ApiClient;
use fltk::{
    app, button, enums::Color, enums::Font, enums::FrameType, frame, group, input, prelude::*, text, window,
};
use std::sync::Arc;
use tokio::runtime::Runtime;

#[derive(Clone, Debug)]
enum UiMsg {
    UpdateMetrics(serde_json::Value),
    UpdateLogs(Vec<serde_json::Value>),
    ChatReply(String),
    ChatError(String),
    StatusMsg(String),
}

fn main() {
    let rt = Arc::new(Runtime::new().unwrap());
    let client = Arc::new(ApiClient::default());

    let app = app::App::default();
    let (s, r) = app::channel::<UiMsg>();

    let mut wind = window::Window::default()
        .with_size(1000, 700)
        .with_label("Qallow Unified AGI Orchestrator");
    wind.set_color(Color::from_hex(0x111318));

    // Top status and controls
    let mut header = frame::Frame::new(20, 20, 960, 40, "QALLOW RUNTIME: UNIFIED 4-PHASE LOOP");
    header.set_label_font(Font::HelveticaBold);
    header.set_label_size(20);
    header.set_label_color(Color::from_hex(0x00ffaa));
    header.set_frame(FrameType::RFlatBox);
    header.set_color(Color::from_hex(0x1a1e28));

    // Phase control buttons
    let mut controls_group = group::Group::new(20, 80, 960, 60, "");
    controls_group.set_frame(FrameType::RFlatBox);
    controls_group.set_color(Color::from_hex(0x1a1e28));

    let phases = [("Phase 1: Elasticity", 1), ("Phase 2: Harmonic", 2), ("Phase 3: Coherence", 3), ("Phase 4: Convergence", 4)];
    for (i, (label, phase_num)) in phases.iter().enumerate() {
        let mut btn = button::Button::new(40 + (i as i32 * 180), 92, 160, 36, *label);
        btn.set_color(Color::from_hex(0x282f40));
        btn.set_label_color(Color::from_hex(0x88ddff));
        btn.set_frame(FrameType::RFlatBox);
        
        let c = client.clone();
        let s_clone = s.clone();
        let rt_clone = rt.clone();
        let p = *phase_num;
        btn.set_callback(move |_| {
            let c = c.clone();
            let s = s_clone.clone();
            rt_clone.spawn(async move {
                if let Ok(_) = c.start_phase(p, 500).await {
                    s.send(UiMsg::StatusMsg(format!("Running Phase {}", p)));
                }
            });
        });
    }

    let mut stop_btn = button::Button::new(780, 92, 160, 36, "Stop Phase");
    stop_btn.set_color(Color::from_hex(0xff5555));
    stop_btn.set_label_color(Color::White);
    stop_btn.set_frame(FrameType::RFlatBox);
    let c = client.clone();
    let s_clone = s.clone();
    let rt_clone = rt.clone();
    stop_btn.set_callback(move |_| {
        let c = c.clone();
        let s = s_clone.clone();
        rt_clone.spawn(async move {
            if let Ok(_) = c.stop_phase().await {
                s.send(UiMsg::StatusMsg("Phase Stopped".to_string()));
            }
        });
    });
    controls_group.end();

    // Biometrics and Metrics display
    let mut metrics_box = text::TextDisplay::new(20, 160, 460, 240, "Live Biometrics & Kernel State");
    metrics_box.set_color(Color::from_hex(0x1a1e28));
    metrics_box.set_text_color(Color::from_hex(0x00ffcc));
    metrics_box.set_text_font(Font::Courier);
    let mut metrics_buf = text::TextBuffer::default();
    metrics_buf.set_text("Waiting for VEYN stream metrics...\n");
    metrics_box.set_buffer(metrics_buf.clone());

    // Audit logs display
    let mut logs_box = text::TextDisplay::new(500, 160, 480, 240, "System Audit Logs");
    logs_box.set_color(Color::from_hex(0x1a1e28));
    logs_box.set_text_color(Color::from_hex(0xffff88));
    logs_box.set_text_font(Font::Courier);
    let mut logs_buf = text::TextBuffer::default();
    logs_buf.set_text("System audit trail ready.\n");
    logs_box.set_buffer(logs_buf.clone());

    // Reasoning Engine Chat
    let mut chat_disp = text::TextDisplay::new(20, 420, 960, 200, "Sovereign AGI Interaction (Gemma 4)");
    chat_disp.set_color(Color::from_hex(0x1a1e28));
    chat_disp.set_text_color(Color::White);
    chat_disp.set_text_font(Font::Helvetica);
    let mut chat_buf = text::TextBuffer::default();
    chat_buf.set_text("Gemma 4 initialized with physiological context prefixing.\n");
    chat_disp.set_buffer(chat_buf.clone());

    let mut chat_input = input::Input::new(20, 630, 800, 40, "");
    chat_input.set_color(Color::from_hex(0x282f40));
    chat_input.set_text_color(Color::White);

    let mut send_btn = button::Button::new(830, 630, 150, 40, "Send / Reason");
    send_btn.set_color(Color::from_hex(0x0055ff));
    send_btn.set_label_color(Color::White);
    send_btn.set_frame(FrameType::RFlatBox);

    let c = client.clone();
    let s_clone = s.clone();
    let rt_clone = rt.clone();
    let mut input_clone = chat_input.clone();
    let mut c_buf_clone = chat_buf.clone();
    send_btn.set_callback(move |_| {
        let msg = input_clone.value();
        if msg.trim().is_empty() { return; }
        input_clone.set_value("");
        c_buf_clone.append(&format!("\nOperator: {}\nReasoning...", msg));
        
        let c = c.clone();
        let s = s_clone.clone();
        rt_clone.spawn(async move {
            match c.chat(&msg).await {
                Ok(reply) => s.send(UiMsg::ChatReply(reply)),
                Err(e) => s.send(UiMsg::ChatError(e.to_string())),
            }
        });
    });

    wind.end();
    wind.show();

    // Spawn background metrics poller
    let c = client.clone();
    let s_clone = s.clone();
    let rt_clone = rt.clone();
    rt_clone.spawn(async move {
        let mut interval = tokio::time::interval(tokio::time::Duration::from_millis(500));
        loop {
            interval.tick().await;
            if let Ok(m) = c.get_metrics().await {
                s_clone.send(UiMsg::UpdateMetrics(m));
            }
            if let Ok(l) = c.get_audit_logs().await {
                s_clone.send(UiMsg::UpdateLogs(l));
            }
        }
    });

    while app.wait() {
        if let Some(msg) = r.recv() {
            match msg {
                UiMsg::UpdateMetrics(val) => {
                    if let Ok(pretty) = serde_json::to_string_pretty(&val) {
                        metrics_buf.set_text(&pretty);
                    }
                }
                UiMsg::UpdateLogs(logs) => {
                    let mut s = String::new();
                    for log in logs.iter().rev().take(15) {
                        if let Some(action) = log.get("action").and_then(|v| v.as_str()) {
                            let detail = log.get("detail").and_then(|v| v.as_str()).unwrap_or("");
                            s.push_str(&format!("• [{}]: {}\n", action, detail));
                        }
                    }
                    if !s.is_empty() {
                        logs_buf.set_text(&s);
                    }
                }
                UiMsg::ChatReply(reply) => {
                    chat_buf.append(&format!("\nQallow: {}\n", reply));
                }
                UiMsg::ChatError(err) => {
                    chat_buf.append(&format!("\n[Error reasoning]: {}\n", err));
                }
                UiMsg::StatusMsg(status) => {
                    header.set_label(&format!("QALLOW RUNTIME: {}", status.to_uppercase()));
                }
            }
        }
    }
}
