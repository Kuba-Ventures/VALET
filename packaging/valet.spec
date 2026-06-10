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
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.getcwd())

# The frontend (built by `npm run build`) and prompt/template assets the backend
# reads at runtime. Bundled as data so the one-file binary is self-contained.
datas = [
    ("frontend/dist", "frontend/dist"),
    ("templates", "templates"),
    ("prompts", "prompts"),
]

# Modules imported lazily / inside functions that PyInstaller's static analysis
# can miss. self_mod is DELIBERATELY EXCLUDED (Stage E: no self-editing in a
# shipped build) — _load_self_mod() returns None when the import fails.
hiddenimports = [
    "licensing", "action_executor", "applescript_executor", "safe_executor",
    "safety", "voice_text", "task_manager", "project_scanner", "design_partner",
    "observability", "sentry_sdk", "anthropic", "httpx", "uvicorn", "uvicorn.lifespan.on",
    "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
]
hiddenimports += collect_submodules("anthropic")

a = Analysis(
    ["server.py"],
    pathex=[ROOT],
    binaries=[],
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
