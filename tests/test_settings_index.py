"""settings_index — spoken settings target → (label, deep-link URL).

Run:  ./.venv/bin/python tests/test_settings_index.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import settings_index


def test_exact_with_settings_suffix():
    hit = settings_index.match_setting("bluetooth settings")
    assert hit is not None and hit[0] == "Bluetooth", hit
    assert hit[1].startswith("x-apple.systempreferences:"), hit


def test_alias_wifi():
    hit = settings_index.match_setting("wifi")
    assert hit and hit[0] == "Wi-Fi", hit


def test_alias_brightness_to_displays():
    hit = settings_index.match_setting("brightness")
    assert hit and hit[0] == "Displays", hit


def test_strip_noise_and_suffix():
    hit = settings_index.match_setting("the system sound preferences")
    assert hit and hit[0] == "Sound", hit


def test_fuzzy_typo():
    hit = settings_index.match_setting("blutooth")
    assert hit and hit[0] == "Bluetooth", hit


def test_unknown_returns_none():
    assert settings_index.match_setting("the pod bay doors") is None
    assert settings_index.match_setting("") is None


def test_every_pane_has_valid_url():
    for label, url in settings_index._PANES.items():
        assert url.startswith("x-apple.systempreferences:"), (label, url)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
