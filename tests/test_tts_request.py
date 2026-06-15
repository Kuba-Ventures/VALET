"""Fish TTS request building — headless unit tests (perceived-latency PR 3).

Covers the pure `_tts_request` payload/header construction (proxy vs direct
paths, latency tier, model placement) and the env-backed `_fish_latency` /
`_fish_model` validators. No network.

Run:  ./.venv/bin/python -m pytest tests/test_tts_request.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import server


def test_proxy_payload_carries_latency_and_speed():
    payload, headers = server._tts_request(
        "hello", "voice123", 1.0, via_proxy=True, latency="balanced", model="",
    )
    assert payload["text"] == "hello"
    assert payload["reference_id"] == "voice123"
    assert payload["format"] == "mp3"
    assert payload["latency"] == "balanced"
    assert payload["speed"] == 1.0           # proxy converts speed→prosody itself
    assert "prosody" not in payload          # not on the proxy path
    assert headers == {}                     # no model header for the proxy path


def test_proxy_model_goes_in_body_not_header():
    payload, headers = server._tts_request(
        "hi", "v", 1.0, via_proxy=True, latency="low", model="s1",
    )
    assert payload["model"] == "s1"          # proxy turns this into the Fish header
    assert "model" not in headers


def test_direct_payload_uses_prosody_and_model_header():
    payload, headers = server._tts_request(
        "hi", "v", 0.9, via_proxy=False, latency="normal", model="s2-pro",
    )
    assert payload["prosody"] == {"speed": 0.9}
    assert "speed" not in payload
    assert payload["latency"] == "normal"
    assert headers["model"] == "s2-pro"      # Fish reads model only from the header


def test_no_model_means_no_model_anywhere():
    payload, headers = server._tts_request(
        "hi", "v", 1.0, via_proxy=False, latency="balanced", model="",
    )
    assert "model" not in payload and "model" not in headers


def test_fish_latency_default_and_validation(monkeypatch):
    monkeypatch.delenv("FISH_LATENCY", raising=False)
    assert server._fish_latency() == "normal"             # default (no quality change)
    for v in ("normal", "balanced", "low"):
        monkeypatch.setenv("FISH_LATENCY", v)
        assert server._fish_latency() == v
    monkeypatch.setenv("FISH_LATENCY", "ludicrous")       # invalid → safe default
    assert server._fish_latency() == "normal"
    monkeypatch.setenv("FISH_LATENCY", "BALANCED")        # case-insensitive
    assert server._fish_latency() == "balanced"


def test_fish_model_default_and_validation(monkeypatch):
    monkeypatch.delenv("FISH_MODEL", raising=False)
    assert server._fish_model() == ""                     # unset → Fish default
    monkeypatch.setenv("FISH_MODEL", "s1")
    assert server._fish_model() == "s1"
    monkeypatch.setenv("FISH_MODEL", "gpt-9")             # invalid → unset
    assert server._fish_model() == ""
