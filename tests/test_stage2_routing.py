"""Stage 2 routing — detect_action_fast resolves files / settings / system
actions on the no-LLM fast path (and doesn't hijack app/conversation phrases).

Imports server (heavy) and exercises the pure sync router; no mdfind runs here
(detect returns a find_file action; the executor does the search).

Run:  ./.venv/bin/python tests/test_stage2_routing.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("FISH_API_KEY", "test")

import server


def test_system_action_lock_tier0():
    a = server.detect_action_fast("lock the screen")
    assert a and a["action"] == "system_action" and a["name"] == "lock_screen", a
    assert a["tier"] == 0, a


def test_system_action_empty_trash_tier1():
    a = server.detect_action_fast("empty the trash")
    assert a and a["action"] == "system_action" and a["name"] == "empty_trash", a
    assert a["tier"] == 1, a


def test_settings_bluetooth():
    a = server.detect_action_fast("go to bluetooth settings")
    assert a and a["action"] == "open_settings" and a["label"] == "Bluetooth", a
    assert a["target"].startswith("x-apple.systempreferences:"), a


def test_settings_displays():
    a = server.detect_action_fast("open display settings")
    assert a and a["action"] == "open_settings" and a["label"] == "Displays", a


def test_find_file_browse_verb():
    a = server.detect_action_fast("find my q2 report")
    assert a and a["action"] == "find_file", a
    assert a["query"] == "q2 report", a


def test_open_with_kind_cue_is_file():
    a = server.detect_action_fast("open my budget spreadsheet")
    assert a and a["action"] == "find_file", a
    assert a["kind"] == "spreadsheet", a


def test_open_plain_phrase_not_hijacked():
    # No app, no file cue → must NOT become a file search (falls to LLM).
    a = server.detect_action_fast("open the pod bay doors")
    assert a is None or a["action"] not in ("find_file", "open_settings", "system_action"), a


def test_home_folder_opens():
    for phrase, target in [("open my downloads folder", "Downloads"),
                           ("open desktop", "Desktop"),
                           ("open my documents", "Documents")]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "open_app" and a["target"] == target, (phrase, a)


def test_open_known_app_still_wins():
    # An installed app must beat the file fallback.
    a = server.detect_action_fast("open safari")
    assert a and a["action"] == "open_app", a


def test_open_gmail_goes_to_web_not_mail_app():
    # "gmail" is ~0.89 similar to "Mail" — the fuzzy app matcher must NOT grab it;
    # it routes to the Gmail website. ("open mail"/"open email" still = Mail app.)
    a = server.detect_action_fast("open gmail")
    assert a and a["action"] == "open_url" and "mail.google.com" in a["target"], a
    for phrase in ("open mail", "open email"):
        b = server.detect_action_fast(phrase)
        assert b and b["action"] == "open_app" and b["target"] == "Mail", (phrase, b)
    # Browser names still open the browser app, not a website.
    s = server.detect_action_fast("open safari")
    assert s and s["action"] == "open_app", s


def test_is_browser_app():
    assert server._is_browser_app("Google Chrome")
    assert server._is_browser_app("Safari")
    assert server._is_browser_app("Arc")
    assert not server._is_browser_app("Finder")
    assert not server._is_browser_app("Mail")
    assert not server._is_browser_app(None)


def test_open_onscreen_email_routes_to_ui_open():
    # "open the email from X" → click the on-screen inbox row, not a lookup.
    for phrase, tgt in [("open the email from Stripe", "email from stripe"),
                        ("open the Stripe email", "stripe email"),
                        ("read the message from Jacques", "message from jacques")]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "ui_open" and a["target"] == tgt, (phrase, a)


def test_open_email_app_still_launches_mail():
    # "open my email" is the Mail app (app-launch), NOT an on-screen click.
    a = server.detect_action_fast("open my email")
    assert a and a["action"] == "open_app" and a["target"] == "Mail", a


def test_bare_email_word_not_hijacked():
    # Too-vague mail phrases (just "message"/"email") fall through, no wild click.
    for phrase in ("read that message", "open the email"):
        a = server.detect_action_fast(phrase)
        assert a is None or a["action"] != "ui_open", (phrase, a)


def test_summarize_screen_routes():
    # "summarize / tldr / what do I need to do" → read the focused content.
    for phrase in ("summarize what I need to do", "summarize this email",
                   "summarize the email", "tl;dr this page", "give me the gist",
                   "give me the gist of the email", "what do I need to do here",
                   "what does this say", "what are my action items",
                   "recap this thread", "summarize the dashboard"):
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "summarize_screen", (phrase, a)


def test_summarize_does_not_hijack_describe_or_research():
    # "what's on my screen" stays describe; an arbitrary "summarize the <topic>"
    # is NOT a screen summary — it falls through to the LLM (research vs screen).
    assert server.detect_action_fast("what's on my screen")["action"] == "describe_screen"
    for phrase in ("summarize the news about openai", "summarize my q3 fishing trip plans"):
        a = server.detect_action_fast(phrase)
        assert a is None or a["action"] != "summarize_screen", (phrase, a)


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
