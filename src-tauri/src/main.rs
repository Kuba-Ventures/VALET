// VALET desktop shell (Tauri v2).
//
// - Single instance: a second launch focuses the existing window instead of
//   stacking another app (no duplicate dock icons).
// - Spawns the PyInstaller backend (`valet-backend`) as a sidecar serving the
//   frontend + API + WS on http://localhost:8340, points the webview at it.
// - Respawns the backend if it exits, but is CRASH-LOOP SAFE: if it keeps dying
//   within a few seconds of spawning (e.g. a port conflict), it stops respawning
//   instead of churning out endless sidecars.
// - Kills the backend on window close.
//
// localhost is a secure context, so the mic works over plain HTTP.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const BACKEND_URL: &str = "http://localhost:8340";

struct Backend {
    child: Mutex<Option<CommandChild>>,
    shutting_down: AtomicBool,
}

fn spawn_backend(app: AppHandle, attempt: u32) {
    let sidecar = app
        .shell()
        .sidecar("valet-backend")
        .expect("valet-backend sidecar missing")
        .env("VALET_SHIPPED", "1")
        // The backend watchdog exits when this shell dies, so an orphaned backend
        // never holds :8340 across a force-quit (e.g. macOS restarting the app
        // after a permission change). std::process::id() is the shell's PID.
        .env("VALET_PARENT_PID", std::process::id().to_string());
    let (mut rx, child) = sidecar.spawn().expect("failed to start backend");
    // Replace the tracked child; kill any previous one so sidecars never pile up.
    if let Some(old) = app.state::<Backend>().child.lock().unwrap().replace(child) {
        let _ = old.kill();
    }
    let spawned_at = Instant::now();

    // Point the window at the backend once it answers.
    let nav = app.clone();
    tauri::async_runtime::spawn(async move {
        for _ in 0..120 {
            if reqwest::get(format!("{BACKEND_URL}/api/config")).await.is_ok() {
                if let Some(win) = nav.get_webview_window("main") {
                    let _ = win.navigate(BACKEND_URL.parse().unwrap());
                }
                return;
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
        eprintln!("[valet] backend did not come up on :8340");
    });

    // Drain output; respawn on exit with crash-loop protection.
    let watch = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    eprintln!("[backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(_) => {
                    let state = watch.state::<Backend>();
                    if state.shutting_down.load(Ordering::SeqCst) {
                        return;
                    }
                    let alive = spawned_at.elapsed().as_secs();
                    // Died almost immediately => crash or port conflict. Give up
                    // after a few attempts instead of spawning endlessly.
                    if alive < 5 && attempt >= 3 {
                        eprintln!("[valet] backend keeps exiting fast; not respawning");
                        return;
                    }
                    let next = if alive < 5 { attempt + 1 } else { 1 };
                    eprintln!("[valet] backend exited (alive={alive}s); respawning ({next})");
                    tokio::time::sleep(Duration::from_millis(800)).await;
                    spawn_backend(watch.clone(), next);
                    return;
                }
                _ => {}
            }
        }
    });
}

/// A force-quit (e.g. macOS restarting the app after a permission change) can
/// leave the previous backend holding :8340, which blocks the next launch. Kill
/// any process listening there so this launch always binds. Deterministic, so it
/// doesn't depend on the backend watchdog noticing the old shell died.
fn free_stale_backend() {
    let _ = std::process::Command::new("sh")
        .arg("-c")
        .arg("P=$(lsof -ti tcp:8340 -sTCP:LISTEN 2>/dev/null); [ -n \"$P\" ] && kill -9 $P 2>/dev/null; exit 0")
        .status();
}

/// Cursor follower (native overlay, stage 3 — first slice).
///
/// A fullscreen, transparent, always-on-top, CLICK-THROUGH window holding a CSS
/// dot (overlay.html). A ~60fps poll loop reads the global cursor position and
/// moves the dot via `window.eval()` — a Rust->webview call, so no Tauri JS API
/// / capabilities are needed. Crucially it moves only the dot's CSS transform,
/// never the WINDOW, so motion stays GPU-smooth (per-frame window moves are the
/// canonical cursor-follow stutter — the eng-review's locked decision).
///
///   cursor (physical px) --/scale--> logical pt --eval--> dot CSS transform
///
/// First slice: the dot is always visible and follows the cursor (so smoothness
/// + click-through can be judged). Showing it only while Vee steers (via the
/// cursor_control events) and global ⌥-Space PTT are the next slices. Primary
/// monitor only for now; multi-monitor mapping is a follow-up.
fn spawn_cursor_overlay(app: AppHandle) {
    // Tauri window sizes are LOGICAL points; the cursor position below is
    // PHYSICAL px, so capture the scale factor to convert between them.
    let (logical_w, logical_h, scale) = app
        .primary_monitor()
        .ok()
        .flatten()
        .map(|m| {
            let s = m.scale_factor();
            let sz = m.size();
            (sz.width as f64 / s, sz.height as f64 / s, s)
        })
        .unwrap_or((1440.0, 900.0, 2.0));

    let overlay = match WebviewWindowBuilder::new(
        &app,
        "overlay",
        WebviewUrl::App("overlay.html".into()),
    )
    .title("VALET cursor")
    .inner_size(logical_w, logical_h)
    .position(0.0, 0.0)
    .resizable(false)
    .decorations(false)
    .transparent(true)
    .always_on_top(true)
    .skip_taskbar(true)
    .shadow(false)
    .focused(false)
    .build()
    {
        Ok(win) => win,
        Err(e) => {
            eprintln!("[valet] cursor overlay failed to build: {e}");
            return;
        }
    };
    // Click-through: the overlay must NEVER intercept clicks or hover. If this is
    // wrong the overlay eats every click (the eng-review's flagged critical gap).
    let _ = overlay.set_ignore_cursor_events(true);

    tauri::async_runtime::spawn(async move {
        loop {
            if let Ok(pos) = app.cursor_position() {
                let x = pos.x / scale;
                let y = pos.y / scale;
                let _ = overlay.eval(&format!(
                    "window.moveDot && window.moveDot({:.1}, {:.1})",
                    x, y
                ));
            }
            tokio::time::sleep(Duration::from_millis(16)).await;
        }
    });
}

fn main() {
    // Note: no single-instance plugin. It was added to stop "duplicate dock
    // icons" that turned out to be stale Launch Services entries, not real second
    // instances, and its lock went stale on a force-quit, blocking reopen. Clean
    // launches are guaranteed instead by free_stale_backend() below.
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Backend {
            child: Mutex::new(None),
            shutting_down: AtomicBool::new(false),
        })
        .setup(|app| {
            free_stale_backend();
            spawn_backend(app.handle().clone(), 1);
            spawn_cursor_overlay(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                let state = window.state::<Backend>();
                state.shutting_down.store(true, Ordering::SeqCst);
                let child = state.child.lock().unwrap().take();
                if let Some(child) = child {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running VALET");
}
