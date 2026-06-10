// VALET desktop shell (Tauri v2).
//
// Architecture: the FastAPI backend (bundled by PyInstaller as the
// `valet-backend` sidecar) serves the frontend + API + WebSocket on
// http://localhost:8340. This shell:
//   1. spawns the sidecar with VALET_SHIPPED=1,
//   2. waits until the backend answers on :8340,
//   3. navigates the main window from the bundled loading page to the backend,
//   4. kills the sidecar on exit.
//
// localhost is a secure context, so the Web Speech API (microphone) works over
// plain HTTP — no TLS cert needed in the packaged app.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{Manager, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use std::sync::Mutex;
use std::time::Duration;

const BACKEND_URL: &str = "http://localhost:8340";

// Hold the child so we can kill it on window close.
struct Backend(Mutex<Option<CommandChild>>);

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            // 1. Spawn the backend sidecar.
            let sidecar = app
                .shell()
                .sidecar("valet-backend")
                .expect("valet-backend sidecar missing")
                .env("VALET_SHIPPED", "1");
            let (mut rx, child) = sidecar.spawn().expect("failed to start backend");
            app.state::<Backend>().0.lock().unwrap().replace(child);

            // Drain sidecar stdout/stderr to the Tauri log (helps first-run debugging).
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stderr(line) | CommandEvent::Stdout(line) = event {
                        eprintln!("[backend] {}", String::from_utf8_lossy(&line));
                    }
                }
            });

            // 2/3. Once the backend answers, point the window at it.
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                for _ in 0..120 {
                    if reqwest::get(format!("{BACKEND_URL}/api/config")).await.is_ok() {
                        if let Some(win) = handle.get_webview_window("main") {
                            let _ = win.navigate(BACKEND_URL.parse().unwrap());
                        }
                        return;
                    }
                    tokio::time::sleep(Duration::from_millis(500)).await;
                }
                eprintln!("[valet] backend did not come up on :8340");
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // 4. Kill the backend when the main window closes.
            if let WindowEvent::CloseRequested { .. } = event {
                if let Some(child) = window
                    .state::<Backend>()
                    .0
                    .lock()
                    .unwrap()
                    .take()
                {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running VALET");
}
