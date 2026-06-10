# Packaging VALET as a signed macOS app (Stage F)

VALET ships as a single, double-clickable, notarized `.app` (Tauri shell +
PyInstaller backend). No vendor secrets ride along — the app talks to the hosted
proxy with the user's license key.

## Architecture

```
┌────────────────────────── VALET.app ──────────────────────────┐
│  Tauri shell (Rust)                                            │
│   ├─ spawns the backend sidecar with VALET_SHIPPED=1           │
│   ├─ waits for http://localhost:8340 to answer                 │
│   └─ webview → http://localhost:8340  (localhost = secure      │
│                                         context, mic works)     │
│  valet-backend (PyInstaller one-file)                          │
│   └─ FastAPI: serves the built frontend + /api + /ws on :8340  │
└────────────────────────────────────────────────────────────────┘
                 │  license-gated calls
                 ▼
        valetvoice.vercel.app  (proxy: Anthropic + Fish keys, metering)
```

Why this shape: the backend already serves the frontend (`FRONTEND_DIST`), so
Tauri is a thin shell. `localhost` is a [secure context], so the Web Speech API
(microphone) works over plain HTTP — no TLS cert in the bundle.

When `VALET_SHIPPED=1`: self-modification is disabled (`self_mod.py` excluded),
and the user `.env` lives at `~/Library/Application Support/VALET/.env` (the app
bundle is read-only) — see `server.py:valet_env_path`.

## Files

| Path | What |
|---|---|
| `packaging/valet.spec` | PyInstaller spec → `dist/valet-backend` (excludes `self_mod`) |
| `src-tauri/tauri.conf.json` | Tauri v2 config (sidecar, bundle, macOS entitlements) |
| `src-tauri/src/main.rs` | spawns the sidecar, waits, navigates the webview, kills on exit |
| `src-tauri/entitlements.plist` | hardened-runtime + Apple-events + network |
| `src-tauri/loading/` | splash shown until the backend is up |
| `packaging/build-macos.sh` | full build → sign → notarize → staple |

## Prerequisites (install once)

```bash
# Rust + Tauri CLI v2
curl https://sh.rustup.rs -sSf | sh
npm i -g @tauri-apps/cli      # v2

# PyInstaller in the project venv (this also repairs venv shebangs after a rename)
bash scripts/setup-build.sh

# App icon (once): generate icons/ from a 1024px PNG
npm exec -- tauri icon path/to/valet-1024.png   # writes src-tauri/icons/
```

You also need, from your **Apple Developer** account:
- a **Developer ID Application** certificate in your login keychain, and
- a notarytool keychain profile:
  ```bash
  xcrun notarytool store-credentials valet-notary \
    --apple-id "you@apple.id" --team-id "TEAMID" --password "app-specific-password"
  ```

## Build

```bash
export SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export NOTARY_PROFILE="valet-notary"
./packaging/build-macos.sh
```

This builds the frontend, PyInstalls the backend, drops it in as the Tauri
sidecar, runs `tauri build` (which signs the `.app` and the sidecar), then
notarizes + staples. Verify Gatekeeper:

```bash
spctl -a -vvv -t install "src-tauri/target/release/bundle/macos/VALET.app"
```

## Ship the download

1. Upload the notarized `.dmg` somewhere public (R2 / S3 / a GitHub release).
2. On the marketing site (Vercel) set **one** env var:
   ```
   DOWNLOAD_URL=https://.../VALET_0.1.0_aarch64.dmg
   ```
   `/api/download` validates the license then 302-redirects there (placeholder
   until set). No code redeploy needed — see `product-site/app/api/download/route.ts`.

## First-run permissions

macOS gates the capabilities VALET needs; the app requests them with plain
explanations on first run (and degrades gracefully if denied):

- **Full Disk Access** — read/act on files anywhere (System Settings → Privacy).
- **Automation** (per app) — drive Calendar/Mail/Notes/Chrome via AppleScript;
  macOS prompts the first time each app is targeted.
- **Accessibility** — only needed once UI-scripting / vision backends land (post-v1).

See `server.py:/api/permissions/status` for the live check the onboarding screen
reads.

## Error reporting

Sentry (app + proxy) is **off until the user consents** (Settings → "Share
crash + error reports"). Payloads are scrubbed — no file contents or message
bodies, only metadata. Set `SENTRY_DSN` (app) / `NEXT_PUBLIC_SENTRY_DSN`
(proxy) to enable.

## Notes / open items

- `restart_self` lives in `self_mod` (excluded when shipped); the packaged app
  should restart via the Tauri shell instead (TODO in the shell).
- `tauri.conf.json` / `Cargo.toml` versions target Tauri v2 — pin to your
  installed CLI version and run `tauri build` once to shake out any schema drift.

[secure context]: https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts
