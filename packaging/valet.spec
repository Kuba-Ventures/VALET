# PyInstaller spec — bundles the VALET FastAPI backend (server.py) into a single
# binary for the Tauri sidecar. Run from the repo root:
#
#   ./.venv/bin/pyinstaller packaging/valet.spec --noconfirm
#
# Output: dist/valet-backend  (a one-file executable that serves the frontend +
# API + WebSocket on http://localhost:8340). Tauri picks it up as a sidecar.
#
# The shipped backend runs with VALET_SHIPPED=1 (set by the Tauri launcher), so
# self-modification is disabled and the user .env lives under
# ~/Library/Application Support/VALET/.env (see server.py:_valet_env_path).

import os
from PyInstaller.utils.hooks import collect_submodules, collect_all

# Repo root. PyInstaller resolves the script + data source paths relative to the
# SPEC file's directory (packaging/), so anchor everything on the repo root that
# contains it — independent of the current working directory.
ROOT = os.path.dirname(SPECPATH)

# The frontend (built by `npm run build`) and prompt/template assets the backend
# reads at runtime. Bundled as data so the one-file binary is self-contained.
datas = [
    (os.path.join(ROOT, "frontend/dist"), "frontend/dist"),
    (os.path.join(ROOT, "templates"), "templates"),
]
# Build stamp (written by build-macos.sh), bundled only if present.
if os.path.exists(os.path.join(ROOT, "build_id.txt")):
    datas.append((os.path.join(ROOT, "build_id.txt"), "."))

# Google OAuth client JSON — bundled at the extraction root so the packaged app
# can run the consent flow. google_auth.py resolves it from _MEIPASS when frozen
# (a user copy in Application Support/VALET still overrides it).
if os.path.exists(os.path.join(ROOT, "google_credentials.json")):
    datas.append((os.path.join(ROOT, "google_credentials.json"), "."))

# Modules imported lazily / inside functions that PyInstaller's static analysis
# can miss. self_mod is DELIBERATELY EXCLUDED (Stage E: no self-editing in a
# shipped build) — _load_self_mod() returns None when the import fails.
hiddenimports = [
    "licensing", "action_executor", "applescript_executor", "safe_executor",
    "safety", "voice_text", "task_manager", "project_scanner", "design_partner",
    "accessibility_executor", "composite_executor", "apple_calendar",
    "contacts_access", "Quartz", "AppKit", "EventKit", "Contacts",
    "observability", "sentry_sdk", "anthropic", "httpx", "uvicorn", "uvicorn.lifespan.on",
    "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
]
hiddenimports += collect_submodules("anthropic")

# pyobjc frameworks (EventKit) need FULL collection — the package data + the
# dynamic Objective-C bindings — not just a bare hidden import, or classes like
# EKEventStore fail to resolve in the frozen build (they show up as "missing
# module" in PyInstaller's warn file and the calendar feature breaks when signed).
_ek_datas, _ek_binaries, _ek_hidden = collect_all("EventKit")
datas += _ek_datas
hiddenimports += _ek_hidden

# Contacts framework — same FULL collection as EventKit so CNContactStore and the
# dynamic Objective-C bindings resolve in the signed build (contacts_access.py).
_cn_datas, _cn_binaries, _cn_hidden = collect_all("Contacts")
datas += _cn_datas
hiddenimports += _cn_hidden

# Google API client libs need FULL collection too: googleapiclient ships static
# discovery documents (data files) and several submodules are imported lazily,
# so a bare hidden import leaves the frozen OAuth flow / Gmail+Calendar calls
# broken. collect_all gathers data + binaries + submodules for each.
_extra_binaries = list(_ek_binaries) + list(_cn_binaries)
for _gpkg in ("googleapiclient", "google_auth_oauthlib", "google.auth",
              "google.oauth2", "google_auth_httplib2", "oauthlib", "requests_oauthlib"):
    try:
        _gd, _gb, _gh = collect_all(_gpkg)
        datas += _gd
        _extra_binaries += _gb
        hiddenimports += _gh
    except Exception as _e:
        print(f"[valet.spec] collect_all({_gpkg!r}) skipped: {_e}")
# google.auth.crypt falls back to pure-python RSA when cryptography is absent;
# include rsa so token signing never hard-fails in the frozen build.
hiddenimports += ["rsa", "google_auth_oauthlib.flow", "googleapiclient.discovery"]

a = Analysis(
    [os.path.join(ROOT, "server.py")],
    pathex=[ROOT],
    binaries=_extra_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["self_mod"],  # self-editing is cut from the shipped build
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="valet-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no terminal window when launched by Tauri
    disable_windowed_traceback=False,
    target_arch=None,       # build per-arch; the build script handles universal
    codesign_identity=None, # Tauri signs the whole .app; the sidecar is signed there
    entitlements_file=None,
)
