fn main() {
    // Declare the app's own commands so the ACL can grant them per-origin (#271).
    //
    // Without this there are no `allow-*` permissions for app commands, so no
    // capability can reference them — and Tauri v2 denies custom commands from a
    // remote origin. Our UI is served by the backend from http://localhost:8340,
    // which IS a remote origin to the webview, so `input_monitoring_granted` was
    // rejected on every real launch and #266 silently fell back to the backend's
    // wrong answer.
    //
    // Each name below autogenerates `allow-$command` / `deny-$command`. NOTE: once
    // an app manifest exists, app commands are ACL-gated from LOCAL origins too, so
    // every command here needs an explicit grant in `capabilities/` — including
    // `tray_action`, which until now was allowed implicitly. The `acl` tests in
    // main.rs prove both are reachable from the origins that actually call them.
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new()
                .commands(&["tray_action", "input_monitoring_granted"]),
        ),
    )
    .expect("failed to run tauri-build");
}
