mod api;
mod auth;
mod compression;
mod config;
mod dispatcher;
mod presence;

use anyhow::Result;
use clap::{Parser, Subcommand};
use tokio::sync::mpsc;
use tracing::{error, info};
use veyn_adapters::{
    ble::BleAdapter, eeg::EegAdapter, healthkit::HealthKitAdapter, mock::MockAdapter, VeynAdapter,
};
use veyn_schemas::VeynEvent;

use api::state::{AppState, PluginInfo};
use auth::ScopedToken;

#[derive(Parser, Debug)]
#[command(
    name    = "veyn",
    version = env!("CARGO_PKG_VERSION"),
    about   = "VEYN daemon — sensory nervous system for software"
)]
struct Cli {
    /// Path to veyn.toml configuration file.
    #[arg(short, long, value_name = "PATH", global = true)]
    config: Option<String>,

    #[command(subcommand)]
    command: Option<Command>,

    /// Override the API port (also overrides VEYN_PORT env and config file).
    #[arg(short, long, value_name = "PORT")]
    port: Option<u16>,

    /// Disable token authentication (development only — do not use in production).
    #[arg(long, default_value_t = false)]
    no_auth: bool,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Run the VEYN daemon (default when no subcommand is given).
    Serve {
        #[arg(short, long, value_name = "PORT")]
        port: Option<u16>,
        #[arg(long, default_value_t = false)]
        no_auth: bool,
    },
    /// Manage WASM plugins.
    Plugin {
        #[command(subcommand)]
        action: PluginCommand,
    },
    /// Run system diagnostics and print a health report.
    Doctor,
}

#[derive(Subcommand, Debug)]
enum PluginCommand {
    /// Install a WASM plugin from a local path into the plugins directory.
    Install {
        /// Path to the plugin .wasm file or plugin directory containing plugin.toml.
        path: String,
        /// Override the destination plugins directory.
        #[arg(long, value_name = "DIR")]
        plugins_dir: Option<String>,
    },
    /// List installed plugins.
    List {
        #[arg(long, value_name = "DIR")]
        plugins_dir: Option<String>,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Some(Command::Plugin { action }) => {
            return run_plugin_command(action, cli.config.as_deref());
        }
        Some(Command::Doctor) => {
            return run_doctor(cli.config.as_deref());
        }
        Some(Command::Serve { port, no_auth }) => {
            return run_serve(cli.config.as_deref(), port, no_auth).await;
        }
        None => {
            // Default: run the daemon with top-level flags.
            return run_serve(cli.config.as_deref(), cli.port, cli.no_auth).await;
        }
    }
}

fn run_plugin_command(action: PluginCommand, config_path: Option<&str>) -> Result<()> {
    let cfg = config::load(config_path, None, false)?;
    match action {
        PluginCommand::Install { path, plugins_dir } => {
            let dest_dir = plugins_dir.unwrap_or_else(|| cfg.plugins_dir.clone());
            std::fs::create_dir_all(&dest_dir)?;

            let src = std::path::Path::new(&path);
            if !src.exists() {
                anyhow::bail!("path does not exist: {}", path);
            }

            if src.is_dir() {
                // Copy entire plugin directory.
                let plugin_name = src.file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("plugin");
                let dest = std::path::Path::new(&dest_dir).join(plugin_name);
                copy_dir_recursive(src, &dest)?;
                println!("Installed plugin directory '{}' → {}", plugin_name, dest.display());
            } else {
                // Copy single .wasm file.
                let file_name = src.file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("plugin.wasm");
                let dest = std::path::Path::new(&dest_dir).join(file_name);
                std::fs::copy(src, &dest)?;
                println!("Installed plugin '{}' → {}", file_name, dest.display());
            }
            Ok(())
        }
        PluginCommand::List { plugins_dir } => {
            let dir = plugins_dir.unwrap_or_else(|| cfg.plugins_dir.clone());
            let path = std::path::Path::new(&dir);
            if !path.exists() {
                println!("No plugins directory at: {}", dir);
                return Ok(());
            }
            let mut found = 0usize;
            for entry in std::fs::read_dir(path)? {
                let entry = entry?;
                let name = entry.file_name();
                println!("  {}", name.to_string_lossy());
                found += 1;
            }
            if found == 0 {
                println!("No plugins installed in: {}", dir);
            }
            Ok(())
        }
    }
}

fn copy_dir_recursive(src: &std::path::Path, dest: &std::path::Path) -> Result<()> {
    std::fs::create_dir_all(dest)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let dest_path = dest.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_dir_recursive(&entry.path(), &dest_path)?;
        } else {
            std::fs::copy(entry.path(), dest_path)?;
        }
    }
    Ok(())
}

fn run_doctor(config_path: Option<&str>) -> Result<()> {
    println!("=== VEYN Doctor ===\n");

    // Config check.
    match config::load(config_path, None, false) {
        Ok(cfg) => {
            println!("[✓] Config loaded (port={}, auth={})", cfg.api_port, cfg.require_auth);

            // Token file check.
            let token_path = cfg.token_path.as_deref()
                .map(std::path::PathBuf::from)
                .unwrap_or_else(auth::token_path);
            if token_path.exists() {
                println!("[✓] Token file exists: {}", token_path.display());
            } else {
                println!("[!] Token file missing: {} (will be created on first start)", token_path.display());
            }

            // Plugins dir check.
            let plugins_path = std::path::Path::new(&cfg.plugins_dir);
            if plugins_path.exists() {
                let count = std::fs::read_dir(plugins_path).map(|d| d.count()).unwrap_or(0);
                println!("[✓] Plugins dir: {} ({} entries)", cfg.plugins_dir, count);
            } else {
                println!("[!] Plugins dir missing: {} (will be created on start)", cfg.plugins_dir);
            }

            // Rules file check.
            if std::path::Path::new(&cfg.rules_path).exists() {
                println!("[✓] Rules file: {}", cfg.rules_path);
            } else {
                println!("[!] Rules file missing: {} (built-in intent rules will be used)", cfg.rules_path);
            }

            // VEYN daemon connectivity.
            let url = format!("http://127.0.0.1:{}/health", cfg.api_port);
            match std::process::Command::new("curl")
                .args(["-sf", "--max-time", "2", &url])
                .output()
            {
                Ok(out) if out.status.success() => {
                    println!("[✓] Daemon responding at {}", url);
                }
                _ => {
                    println!("[!] Daemon not reachable at {} (not running?)", url);
                }
            }
        }
        Err(e) => {
            println!("[✗] Config error: {}", e);
        }
    }

    println!("\nDone.");
    Ok(())
}

async fn run_serve(config_path: Option<&str>, port: Option<u16>, no_auth: bool) -> Result<()> {
    let cfg = config::load(config_path, port, no_auth)?;

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| cfg.log_level.as_str().into()),
        )
        .init();

    info!(
        api_port              = cfg.api_port,
        healthkit_port        = cfg.healthkit_port,
        mock_mode             = cfg.mock_mode,
        ble_enabled           = cfg.ble_enabled,
        eeg_enabled           = cfg.eeg_enabled,
        plugins_dir           = %cfg.plugins_dir,
        mqtt_enabled          = cfg.mqtt_url.is_some(),
        presence_timeout_secs = cfg.presence_timeout_secs,
        require_auth          = cfg.require_auth,
        rules_path            = %cfg.rules_path,
        "VEYN daemon starting"
    );

    // Load or generate the bearer token.
    let token_secret = auth::load_or_create_token(cfg.token_path.as_deref())?;
    let primary_token = ScopedToken::parse(&token_secret);
    if cfg.require_auth {
        info!("Auth enabled — token path: {:?}", auth::token_path());
    } else {
        tracing::warn!("Auth DISABLED — do not use in production");
    }

    let (event_tx, event_rx) = mpsc::channel::<VeynEvent>(1_024);
    let state = AppState::new(primary_token, cfg.clone());

    // Dispatcher.
    {
        let state = state.clone();
        let path = cfg.jsonl_path.clone();
        tokio::spawn(async move {
            dispatcher::run(event_rx, state, path).await;
        });
    }

    // Presence detection.
    {
        let state = state.clone();
        let tx = event_tx.clone();
        let timeout_ms = (cfg.presence_timeout_secs * 1_000) as i64;
        tokio::spawn(async move {
            presence::run(state, tx, timeout_ms).await;
        });
    }

    // Mock adapter.
    if cfg.mock_mode {
        spawn_adapter(MockAdapter, event_tx.clone());
    }

    // HealthKit TCP relay.
    spawn_adapter(
        HealthKitAdapter::new(cfg.healthkit_port, state.notification_tx.clone()),
        event_tx.clone(),
    );

    // BLE adapter.
    if cfg.ble_enabled {
        spawn_adapter(BleAdapter, event_tx.clone());
    }

    // EEG/OSC adapter.
    if cfg.eeg_enabled {
        spawn_adapter(EegAdapter::new(cfg.osc_port), event_tx.clone());
    }

    // WASM plugin adapters.
    let plugin_adapters = veyn_plugins::discover_adapters(&cfg.plugins_dir);
    if plugin_adapters.is_empty() {
        info!(plugins_dir = %cfg.plugins_dir, "no WASM plugins found");
    }
    for plugin in plugin_adapters {
        state.register_plugin(PluginInfo {
            name: plugin.manifest.plugin.name.clone(),
            version: plugin.manifest.plugin.version.clone(),
            description: plugin.manifest.plugin.description.clone(),
        });
        spawn_adapter(plugin, event_tx.clone());
    }

    // Smart home MQTT bridge.
    if let Some(mqtt_url) = cfg.mqtt_url.clone() {
        let rx = state.broadcast_tx.subscribe();
        tokio::spawn(async move {
            if let Err(e) = veyn_adapters::mqtt::run(rx, mqtt_url).await {
                error!("MQTT bridge error: {}", e);
            }
        });
    }

    // Graceful shutdown signal.
    let shutdown = async {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                info!("received SIGINT — shutting down gracefully");
            }
            _ = sigterm() => {
                info!("received SIGTERM — shutting down gracefully");
            }
        }
    };

    // REST + WebSocket API — blocks until the server exits or a shutdown signal arrives.
    let port = cfg.api_port;
    if let Err(e) = api::serve(state, port, shutdown).await {
        error!("API server error: {}", e);
    }

    info!("VEYN daemon stopped");
    Ok(())
}

/// Spawn an adapter with hot-plug / auto-restart semantics.
/// On failure the adapter is restarted with exponential back-off (1 s → 64 s).
/// The loop exits only if the event channel is closed (daemon shutting down).
fn spawn_adapter<A: VeynAdapter + 'static>(adapter: A, tx: mpsc::Sender<VeynEvent>) {
    use std::sync::Arc;
    let adapter = Arc::new(adapter);
    let name = adapter.name().to_owned();
    tokio::spawn(async move {
        let mut backoff_secs = 1u64;
        loop {
            if tx.is_closed() {
                info!(adapter = %name, "channel closed — adapter loop exiting");
                return;
            }
            match adapter.start(tx.clone()).await {
                Ok(()) => {
                    info!(adapter = %name, "adapter exited cleanly — hot-plug: waiting for reconnect");
                    // Short pause before trying to restart to allow device to re-enumerate.
                    tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
                }
                Err(e) => {
                    error!(adapter = %name, backoff_secs, "adapter error: {} — restarting in {}s", e, backoff_secs);
                    tokio::time::sleep(tokio::time::Duration::from_secs(backoff_secs)).await;
                    backoff_secs = (backoff_secs * 2).min(64);
                }
            }
        }
    });
}

async fn sigterm() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        if let Ok(mut s) = signal(SignalKind::terminate()) {
            s.recv().await;
        } else {
            std::future::pending::<()>().await;
        }
    }
    #[cfg(not(unix))]
    std::future::pending::<()>().await
}
