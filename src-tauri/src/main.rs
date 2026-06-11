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
use tauri::{AppHandle, Manager, WindowEvent};
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
        .env("VALET_SHIPPED", "1");
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

fn main() {
    tauri::Builder::default()
        // MUST be the first plugin. A second launch focuses the existing window
        // instead of opening another instance (no duplicate dock icons).
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.show();
                let _ = win.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .manage(Backend {
            child: Mutex::new(None),
            shutting_down: AtomicBool::new(false),
        })
        .setup(|app| {
            spawn_backend(app.handle().clone(), 1);
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
