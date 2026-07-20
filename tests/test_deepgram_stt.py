"""Deepgram streaming STT — the offline-testable parts.

No network, no key, no audio. What's pinned here is the wiring that is easy to
get quietly wrong: the audio contract, keyword-boost parameter selection, and
the rule that a missing key degrades quality rather than removing voice.

Run:  ./.venv/bin/python -m pytest tests/test_deepgram_stt.py -q
"""

import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

import deepgram_stt as dg


def _params(url):
    return parse_qs(urlparse(url).query)


def test_missing_key_means_fall_back_not_fail(monkeypatch):
    """No key must mean "keep using the built-in recognizer". Treating it as an
    error would leave the user with no voice at all — strictly worse than a
    worse recognizer."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    assert dg.available() is False
    monkeypatch.setenv("DEEPGRAM_API_KEY", "x" * 40)
    assert dg.available() is True
    monkeypatch.setenv("DEEPGRAM_API_KEY", "   ")     # whitespace is not a key
    assert dg.available() is False


def test_audio_contract_is_explicit():
    """The frontend has to resample to exactly this or transcripts come back as
    gibberish rather than as an error."""
    assert dg.SAMPLE_RATE == 16000
    assert dg.ENCODING == "linear16"
    assert dg.CHANNELS == 1
    p = _params(dg._build_url([]))
    assert p["sample_rate"] == ["16000"]
    assert p["encoding"] == ["linear16"]
    assert p["channels"] == ["1"]


def test_keyword_boost_param_matches_the_model():
    """nova-3 calls it `keyterm`, nova-2 calls it `keywords`. Sending the wrong
    one is silently accepted by the service and simply does nothing."""
    p3 = _params(dg._build_url(["GitHub", "Vee"], model="nova-3"))
    assert p3["keyterm"] == ["GitHub", "Vee"] and "keywords" not in p3
    p2 = _params(dg._build_url(["GitHub"], model="nova-2"))
    assert p2["keywords"] == ["GitHub"] and "keyterm" not in p2


def test_interim_results_are_on():
    """Without interim results the caption only appears after you stop talking,
    which reads as lag even when the final transcript is fast."""
    p = _params(dg._build_url([]))
    assert p["interim_results"] == ["true"]


def test_keyterms_include_whats_on_screen():
    """The context half is the point: a general model has no reason to prefer
    "GitHub" over "ghetto", but if a GitHub window is open it is overwhelmingly
    the likelier word."""
    windows = [
        {"app": "Slack", "title": "channel", "url": ""},
        {"app": "Google Chrome", "title": "Pull Request #319",
         "url": "https://github.com/Kuba-Ventures/VALET/pull/319"},
    ]
    terms = [t.lower() for t in dg.keyterms_for_context(windows)]
    assert "github" in terms          # from the URL, not the title
    assert "slack" in terms
    assert "vee" in terms             # the wake word collides with the/be/see


def test_keyterms_are_capped():
    """Deepgram bounds how many terms it accepts; blowing the budget on window
    titles would crowd out the ones that actually disambiguate."""
    many = [{"app": f"App{i}", "title": "", "url": f"https://site{i}.com"}
            for i in range(80)]
    assert len(dg.keyterms_for_context(many)) <= dg._MAX_KEYTERMS


def test_domain_vocabulary_covers_the_known_failures():
    terms = {t.lower() for t in dg._DOMAIN_TERMS}
    for word in ("github", "vee", "slack", "gmail", "cursor"):
        assert word in terms, word
