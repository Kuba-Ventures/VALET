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
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    ActivationPolicy, AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const BACKEND_URL: &str = "http://localhost:8340";

struct Backend {
    child: Mutex<Option<CommandChild>>,
    shutting_down: AtomicBool,
    // When the tray popover was last hidden. Clicking the menu-bar icon while the
    // popover is open blurs it (→ hide) BEFORE the tray click event arrives, so
    // without this guard that same click would instantly reopen it. We swallow a
    // show that lands within a short window of a hide, making the icon a toggle.
    popover_hidden_at: Mutex<Option<Instant>>,
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

/// One overlay per display. Bounds are in the global LOGICAL (point) space —
/// the only coordinate space macOS keeps consistent across mixed-DPI displays.
struct CursorOverlay {
    win: tauri::WebviewWindow,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

/// Cursor follower (native overlay) — multi-monitor.
///
/// One transparent, always-on-top, CLICK-THROUGH window per display (overlay.html
/// holds a CSS dot). A ~60fps poll loop reads the global cursor position, finds the
/// display it's on, and moves that overlay's dot via `window.eval()` (a
/// Rust->webview call, so no Tauri JS API / capabilities needed). Only the dot's
/// CSS transform moves, never the WINDOW, so motion stays GPU-smooth.
///
///   cursor (physical, global) --minus display origin /scale--> local CSS pt
///
/// Each overlay is placed in PHYSICAL coordinates exactly over its display, so a
/// secondary monitor with a different scale factor still maps correctly. The dot
/// shows only on the display the cursor is currently on.
fn spawn_cursor_overlay(app: AppHandle) {
    let monitors = match app.available_monitors() {
        Ok(m) if !m.is_empty() => m,
        _ => {
            eprintln!("[valet] cursor overlay: no monitors found");
            return;
        }
    };

    // macOS keeps ONE consistent global coordinate space in LOGICAL POINTS, and
    // `cursor_position()` reports those points scaled by the PRIMARY display's
    // scale factor (so on a 2x primary, cursor.x is points*2). Convert the cursor
    // to global points by dividing by the primary scale, then everything — window
    // placement and hit-testing — happens in points. `monitor.position()` is
    // already in points; `monitor.size()` is physical px, so /scale → point size.
    let primary_scale = app
        .primary_monitor()
        .ok()
        .flatten()
        .map(|m| m.scale_factor())
        .unwrap_or(2.0);
    eprintln!(
        "[valet] cursor overlay: {} monitor(s), primary_scale={}",
        monitors.len(),
        primary_scale
    );

    let mut overlays: Vec<CursorOverlay> = Vec::new();
    for (i, m) in monitors.iter().enumerate() {
        let scale = m.scale_factor();
        let pos = m.position(); // LOGICAL points (global)
        let size = m.size(); // physical px
        let lx = pos.x as f64;
        let ly = pos.y as f64;
        let lw = size.width as f64 / scale; // point size
        let lh = size.height as f64 / scale;
        eprintln!(
            "[valet]   monitor {i}: origin=({lx},{ly}) size={lw}x{lh} pts scale={scale}"
        );
        let win = match WebviewWindowBuilder::new(
            &app,
            format!("overlay-{i}"),
            WebviewUrl::App("overlay.html".into()),
        )
        .title("VALET cursor")
        .inner_size(lw, lh)
        .position(lx, ly) // LOGICAL placement in the global point space
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
                eprintln!("[valet] cursor overlay {i} failed to build: {e}");
                continue;
            }
        };
        // Click-through: the overlay must NEVER intercept clicks or hover.
        let _ = win.set_ignore_cursor_events(true);

        overlays.push(CursorOverlay { win, x: lx, y: ly, w: lw, h: lh });
    }
    if overlays.is_empty() {
        return;
    }

    tauri::async_runtime::spawn(async move {
        let mut last_active: Option<usize> = None;
        loop {
            if let Ok(p) = app.cursor_position() {
                // Cursor → global points.
                let gx = p.x / primary_scale;
                let gy = p.y / primary_scale;
                let active = overlays.iter().position(|ov| {
                    gx >= ov.x && gx < ov.x + ov.w && gy >= ov.y && gy < ov.y + ov.h
                });
                // Hide the dot on the display the cursor just left.
                if active != last_active {
                    if let Some(prev) = last_active {
                        if let Some(ov) = overlays.get(prev) {
                            let _ = ov.win.eval("window.setDotVisible&&window.setDotVisible(false)");
                        }
                    }
                    last_active = active;
                }
                if let Some(i) = active {
                    let ov = &overlays[i];
                    let lx = gx - ov.x; // local point coords within the window
                    let ly = gy - ov.y;
                    let _ = ov.win.eval(&format!(
                        "window.moveDot&&window.moveDot({:.1},{:.1});window.setDotVisible&&window.setDotVisible(true)",
                        lx, ly
                    ));
                }
            }
            tokio::time::sleep(Duration::from_millis(16)).await;
        }
    });
}

/// Drive the frontend's push-to-talk from a chord event. Reuses the exact WS
/// `ptt` path the frontend already has, via a small `window.__valetChord` hook —
/// a Rust->webview eval (like the cursor overlay), so no Tauri JS API needed.
fn emit_chord(app: &AppHandle, state: &str) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.eval(&format!(
            "window.__valetChord&&window.__valetChord('{state}')"
        ));
    }
}

/// Global ⌃⌥ push-to-talk tap, in the MAIN app process.
///
/// macOS gates key monitoring behind Input Monitoring, granted PER EXECUTABLE.
/// Running the tap here (the stable "VALET" binary) means one promptable, durable
/// grant — unlike the backend helper, whose hash (and thus grant) churns each
/// build, so its tap silently received no events. The tap is LISTEN-ONLY, so ⌃⌥
/// keeps working everywhere. A FlagsChanged tap reads the Control/Option modifier
/// bits directly (no main-thread-only keycode→string resolution), so it runs fine
/// on this background thread and returns gracefully if the grant is missing.
/// Chord semantics mirror the in-window handler: down when both held, up on
/// release, cancel if another key is pressed mid-hold (a real ⌃⌥-letter shortcut).
fn spawn_global_chord(app: AppHandle) {
    use core_foundation::runloop::{kCFRunLoopCommonModes, CFRunLoop};
    use core_graphics::event::{
        CGEventFlags, CGEventTap, CGEventTapLocation, CGEventTapOptions, CGEventTapPlacement,
        CGEventType, CallbackResult,
    };

    std::thread::spawn(move || {
        let active = AtomicBool::new(false);
        let cancelled = AtomicBool::new(false);

        let tap = CGEventTap::new(
            CGEventTapLocation::HID,
            CGEventTapPlacement::HeadInsertEventTap,
            CGEventTapOptions::ListenOnly,
            vec![CGEventType::FlagsChanged, CGEventType::KeyDown],
            move |_proxy, etype, event| {
                match etype {
                    CGEventType::FlagsChanged => {
                        let f = event.get_flags();
                        let chord = f.contains(CGEventFlags::CGEventFlagControl)
                            && f.contains(CGEventFlags::CGEventFlagAlternate);
                        if chord && !active.load(Ordering::Relaxed) {
                            active.store(true, Ordering::Relaxed);
                            cancelled.store(false, Ordering::Relaxed);
                            emit_chord(&app, "down");
                        } else if !chord && active.load(Ordering::Relaxed) {
                            let was_cancelled = cancelled.load(Ordering::Relaxed);
                            active.store(false, Ordering::Relaxed);
                            cancelled.store(false, Ordering::Relaxed);
                            if !was_cancelled {
                                emit_chord(&app, "up");
                            }
                        }
                    }
                    CGEventType::KeyDown => {
                        // A non-modifier key while the chord is held = a real
                        // ⌃⌥-letter shortcut → cancel and discard.
                        if active.load(Ordering::Relaxed) && !cancelled.load(Ordering::Relaxed) {
                            cancelled.store(true, Ordering::Relaxed);
                            emit_chord(&app, "cancel");
                        }
                    }
                    _ => {}
                }
                CallbackResult::Keep // listen-only: pass the event through untouched
            },
        );

        let tap = match tap {
            Ok(t) => t,
            Err(_) => {
                eprintln!(
                    "[valet] global ⌃⌥ tap off — grant VALET Input Monitoring in \
                     System Settings to talk from any app (the in-window chord still works)."
                );
                return;
            }
        };
        let source = match tap.mach_port().create_runloop_source(0) {
            Ok(s) => s,
            Err(_) => {
                eprintln!("[valet] global ⌃⌥ tap: failed to create run-loop source");
                return;
            }
        };
        CFRunLoop::get_current().add_source(&source, unsafe { kCFRunLoopCommonModes });
        tap.enable();
        eprintln!("[valet] global ⌃⌥ tap active — hold ⌃⌥ in any app to talk");
        CFRunLoop::run_current();
    });
}

/// Clean shutdown shared by the tray "Quit" item and the window-close handler:
/// stop the respawn watchdog and kill the backend sidecar so nothing is orphaned.
fn shutdown_backend(app: &AppHandle) {
    let state = app.state::<Backend>();
    state.shutting_down.store(true, Ordering::SeqCst);
    let child = state.child.lock().unwrap().take();
    if let Some(child) = child {
        let _ = child.kill();
    }
}

/// Bring the orb popover to the front (tray "Open VALET" + left-click). The orb
/// is the always-on status surface, so the tray opens it rather than toggling it.
/// If the saved position lands the window on a DIFFERENT monitor than the one the
/// user is on (cursor / where they clicked the tray), re-center it on the active
/// monitor — otherwise it occasionally opens on a side display. When it's already
/// on the active monitor, its position is left untouched (respects manual drags).
fn show_main(app: &AppHandle) {
    let Some(win) = app.get_webview_window("main") else { return };
    reposition_to_active_monitor(app, &win);
    let _ = win.show();
    let _ = win.set_focus();
}

/// Move `win` to the monitor under the cursor, centered, but only if it isn't
/// already on that monitor. Coordinate handling mirrors the cursor overlay:
/// macOS keeps one global space in LOGICAL POINTS, `cursor_position()` reports
/// points scaled by the PRIMARY monitor's factor, `monitor.position()` is points,
/// and `monitor.size()` is physical px (→ /scale for point size).
fn reposition_to_active_monitor(app: &AppHandle, win: &tauri::WebviewWindow) {
    let (Ok(cursor), Ok(monitors)) = (app.cursor_position(), app.available_monitors()) else {
        return;
    };
    let primary_scale = app
        .primary_monitor()
        .ok()
        .flatten()
        .map(|m| m.scale_factor())
        .unwrap_or(2.0);
    let gx = cursor.x / primary_scale;
    let gy = cursor.y / primary_scale;

    // Find the monitor (as a point rect) containing the cursor.
    let target = monitors.iter().find_map(|m| {
        let scale = m.scale_factor();
        let pos = m.position();
        let mx = pos.x as f64;
        let my = pos.y as f64;
        let mw = m.size().width as f64 / scale;
        let mh = m.size().height as f64 / scale;
        if gx >= mx && gx < mx + mw && gy >= my && gy < my + mh {
            Some((mx, my, mw, mh))
        } else {
            None
        }
    });
    let Some((mx, my, mw, mh)) = target else { return };

    // Already on this monitor? Leave the user's position alone.
    if let Ok(Some(cur)) = win.current_monitor() {
        let cp = cur.position();
        if (cp.x as f64 - mx).abs() < 1.0 && (cp.y as f64 - my).abs() < 1.0 {
            return;
        }
    }

    // Center the fixed-size (380×560) window on the active monitor (point coords).
    let (win_w, win_h) = (380.0_f64, 560.0_f64);
    let tx = mx + (mw - win_w) / 2.0;
    let ty = my + (mh - win_h) / 2.0;
    let _ = win.set_position(tauri::LogicalPosition::new(tx, ty));
}

/// Run a tray-menu action by id. Shared by the custom HTML popover (`tray_action`
/// command) so the menu items behave identically to the old native NSMenu items.
fn handle_tray_action(app: &AppHandle, id: &str) {
    match id {
        "open" => show_main(app),
        "toggle_listen" => {
            // Flip Active/Asleep via the SAME path as the in-orb toggle (one
            // source of truth); the orb shows the live status.
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.eval(
                    "window.__valetToggleListening && window.__valetToggleListening()");
            }
        }
        "settings" => {
            // Open the in-app settings panel on the active monitor, then call the
            // frontend hook via eval (no Tauri JS API needed).
            show_main(app);
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.eval(
                    "window.__valetOpenSettings && window.__valetOpenSettings()");
            }
        }
        "replay_setup" => {
            // Re-run the first-run wizard on demand (no reload, so the AudioContext
            // stays unlocked and Vee starts narrating immediately).
            show_main(app);
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.eval(
                    "window.__valetReplayOnboarding && window.__valetReplayOnboarding()");
            }
        }
        "account" => {
            // Account/billing/license live on the web (IA invariant 2).
            let _ = std::process::Command::new("open")
                .arg("https://valetvoice.vercel.app/account")
                .spawn();
        }
        "restart" => {
            // Restart the backend via its endpoint (Tauri shell respawns the
            // sidecar). Spawned so the handler returns promptly.
            tauri::async_runtime::spawn(async {
                let _ = reqwest::Client::new()
                    .post(format!("{BACKEND_URL}/api/restart"))
                    .send()
                    .await;
            });
        }
        "quit" => {
            shutdown_backend(app);
            app.exit(0);
        }
        _ => {}
    }
}

/// Invoked from the custom tray popover (tray-menu.html) when an item is clicked.
#[tauri::command]
fn tray_action(app: AppHandle, id: String) {
    handle_tray_action(&app, &id);
}

/// Show the custom HTML tray popover anchored directly under the menu-bar icon.
/// Positioning is handled by tauri-plugin-positioner from the cached tray rect,
/// so it lands correctly on any monitor / scale / bar position. Toggles: a
/// second click while visible hides it (native-menu behavior).
fn show_tray_popover(app: &AppHandle) {
    use tauri_plugin_positioner::{Position, WindowExt};

    let Some(win) = app.get_webview_window("tray_menu") else { return };
    if win.is_visible().unwrap_or(false) {
        let _ = win.hide();
        return;
    }
    // If we just hid (because this very click blurred the open popover), don't
    // reopen — treat the icon as a toggle.
    if let Some(t) = *app.state::<Backend>().popover_hidden_at.lock().unwrap() {
        if t.elapsed() < Duration::from_millis(250) {
            return;
        }
    }
    // Center the popover just below the clicked icon, clamped to the screen.
    let _ = win.move_window(Position::TrayBottomCenter);
    let _ = win.show();
    let _ = win.set_focus();
}

fn main() {
    // Note: no single-instance plugin. It was added to stop "duplicate dock
    // icons" that turned out to be stale Launch Services entries, not real second
    // instances, and its lock went stale on a force-quit, blocking reopen. Clean
    // launches are guaranteed instead by free_stale_backend() below.
    // Persist only the orb widget's dragged POSITION across launches, never its
    // size — the window is fixed-size (resizable:false, 380x560), and restoring a
    // stale persisted size made it open tiny. POSITION-only keeps the configured
    // size every launch. The per-display cursor `overlay-N` windows are placed
    // explicitly each launch, so exclude them from state restore.
    let window_state = {
        let mut b = tauri_plugin_window_state::Builder::default()
            .with_state_flags(tauri_plugin_window_state::StateFlags::POSITION);
        for i in 0..16 {
            b = b.skip_initial_state(&format!("overlay-{i}"));
        }
        b.build()
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_positioner::init())
        .plugin(window_state)
        .manage(Backend {
            child: Mutex::new(None),
            shutting_down: AtomicBool::new(false),
            popover_hidden_at: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![tray_action])
        .setup(|app| {
            // Menu-bar product: no Dock icon. The orb lives as an always-on-top
            // popover summoned via ⌃⌥ and toggled from the tray.
            app.set_activation_policy(ActivationPolicy::Accessory);

            free_stale_backend();
            spawn_backend(app.handle().clone(), 1);
            spawn_cursor_overlay(app.handle().clone());
            spawn_global_chord(app.handle().clone());

            // Menu bar = status + toggles + links only, never a form (settings IA
            // invariant 3): Open VALET · Toggle Listening · Settings… · Account ↗ ·
            // Restart server · Restart onboarding · Quit. The macOS status-bar
            // dropdown is a system-owned surface that follows the SYSTEM appearance
            // (dark menu in Dark Mode) and exposes no supported way to force it
            // white — Tauri 2 also seals its underlying NSMenu handle. So instead of
            // a native NSMenu we render our OWN borderless webview popover
            // (loading/tray-menu.html), which we style white unconditionally. The
            // tray icon left-click toggles it; items call back via the `tray_action`
            // command into the same handler the native menu used.
            let popover = WebviewWindowBuilder::new(
                app,
                "tray_menu",
                WebviewUrl::App("tray-menu.html".into()),
            )
            .title("VALET menu")
            .inner_size(232.0, 286.0)
            .resizable(false)
            .decorations(false)
            .transparent(true) // window is transparent; the card draws its own shadow
            .always_on_top(true)
            .skip_taskbar(true)
            .shadow(false)
            .visible(false)
            .focused(false)
            .build();
            if let Err(e) = popover {
                eprintln!("[valet] tray popover failed to build: {e}");
            }

            // Monochrome orb mark (black on transparent), marked as a TEMPLATE so
            // macOS renders it in the menu-bar text colour — black on a light bar,
            // white on a dark bar — instead of the blue-on-black app icon. Embedded
            // at compile time (default_window_icon() can be None in dev). No native
            // `.menu()` — we summon the custom popover from the click event instead.
            let tray = TrayIconBuilder::with_id("valet-tray")
                .icon(tauri::include_image!("icons/tray@2x.png"))
                .icon_as_template(true)
                .show_menu_on_left_click(false)
                .on_tray_icon_event(|tray, event| {
                    // Let the positioner cache the icon's rect for THIS event so
                    // it can anchor the popover correctly on any monitor / scale.
                    tauri_plugin_positioner::on_tray_event(tray.app_handle(), &event);
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_tray_popover(tray.app_handle());
                    }
                })
                .build(app);
            // Don't let a tray failure take down the whole app — log it and keep
            // the orb running so we can still see what's wrong.
            if let Err(e) = tray {
                eprintln!("[valet] tray icon failed to build: {e}");
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // The custom tray popover dismisses on focus loss (click elsewhere /
            // Esc), matching native menu behavior. The webview also hides itself on
            // blur; this is the Rust-side backstop.
            if window.label() == "tray_menu" {
                if let WindowEvent::Focused(false) = event {
                    let _ = window.hide();
                    *window.app_handle().state::<Backend>().popover_hidden_at.lock().unwrap() =
                        Some(Instant::now());
                }
                return;
            }
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Menu-bar app: closing the orb HIDES it (quit is via the tray), so
                // the app keeps living in the menu bar instead of exiting. Other
                // windows (e.g. the cursor overlay) tear down the backend as before.
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                } else {
                    shutdown_backend(window.app_handle());
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while running VALET")
        .run(|app, event| {
            // Menu-bar app: do NOT quit when the last window closes — live in the
            // tray. Without this, Tauri's default exits the app on zero windows.
            // BUT a deliberate tray "Quit" calls shutdown_backend() (which sets
            // shutting_down) and then app.exit(0); we must let THAT exit through,
            // or the app hangs on screen with its backend already killed
            // ("backend not connected").
            if let tauri::RunEvent::ExitRequested { api, .. } = event {
                if !app.state::<Backend>().shutting_down.load(Ordering::SeqCst) {
                    api.prevent_exit();
                }
            }
        });
}
