// VALET desktop shell (Tauri v2).
//
// Architecture: the FastAPI backend (bundled by PyInstaller as the
// `valet-backend` sidecar) serves the frontend + API + WebSocket on
// http://localhost:8340. This shell:
//   1. spawns the sidecar with VALET_SHIPPED=1,
//   2. waits until the backend answers on :8340, then points the webview at it,
//   3. respawns the sidecar if it exits unexpectedly (this is how "restart
//      yourself" works in a packaged build — the backend exits, we relaunch it),
//   4. kills the sidecar on window close.
//
// localhost is a secure context, so the Web Speech API (microphone) works over
// plain HTTP — no TLS cert needed in the packaged app.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{AppHandle, Manager, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const BACKEND_URL: &str = "http://localhost:8340";

struct Backend {
    child: Mutex<Option<CommandChild>>,
    shutting_down: AtomicBool,
}

fn spawn_backend(app: AppHandle) {
    let sidecar = app
        .shell()
        .sidecar("valet-backend")
        .expect("valet-backend sidecar missing")
        .env("VALET_SHIPPED", "1");
    let (mut rx, child) = sidecar.spawn().expect("failed to start backend");
    app.state::<Backend>().child.lock().unwrap().replace(child);

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

    // Drain output; respawn on unexpected exit so "restart yourself" works.
    let watch = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    eprintln!("[backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(_) => {
                    let state = watch.state::<Backend>();
                    if !state.shutting_down.load(Ordering::SeqCst) {
                        eprintln!("[valet] backend exited; respawning");
                        tokio::time::sleep(Duration::from_millis(300)).await;
                        spawn_backend(watch.clone());
                    }
                    return;
                }
                _ => {}
            }
        }
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Backend {
            child: Mutex::new(None),
            shutting_down: AtomicBool::new(false),
        })
        .setup(|app| {
            spawn_backend(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            // Kill the backend (and suppress respawn) when the window closes.
            if let WindowEvent::CloseRequested { .. } = event {
                let state = window.state::<Backend>();
                state.shutting_down.store(true, Ordering::SeqCst);
                if let Some(child) = state.child.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running VALET");
}
