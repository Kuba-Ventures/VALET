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


def test_gmail_goal_scope_extracts_named_account():
    # The guided login auto-picks the account whenever the goal names one, so the
    # user isn't asked "which account?" for something they already said (they had to
    # re-answer 3x before this). _gmail_goal_scope must pull "work" out of every
    # phrasing that reaches the login — whether the account word sits right after
    # "go to" or later, after "summarize".
    for g in ("go to my work gmail and summarize July 18",
              "go to gmail and summarize my work email from july 18",
              "sign into my work gmail"):
        assert server._gmail_goal_scope(g)[0] == "work", g
    assert server._gmail_goal_scope("sign into my personal gmail")[0] == "personal"
    # No account named → "" so the login still asks rather than guessing.
    assert server._gmail_goal_scope("go to gmail and summarize today's emails")[0] == ""


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


def test_go_to_clicks_on_screen_target():
    # "go to / navigate to / select X" with no app/web/settings match → click X
    # on the current screen (e.g. the "Developers" card on a Stripe page).
    for phrase, tgt in [("go to developers", "developers"),
                        ("navigate to developers", "developers"),
                        ("select developers", "developers"),
                        ("go to the developers section", "developers"),
                        ("go to billing", "billing")]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "ui_act" and a["ui_action"] == "click" \
            and a["target"] == tgt, (phrase, a)


def test_set_value_clicks_on_screen_option():
    # "set/change/switch <setting> to <value>" → click the VALUE on screen
    # (e.g. the Dark button under Appearance on the Stripe Developers page).
    for phrase, val in [("set appearance to dark", "dark"),
                        ("change appearance to dark", "dark"),
                        ("switch to dark mode", "dark"),
                        ("change the theme to light", "light"),
                        ("set the SDK language to python", "python")]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "ui_act" and a["ui_action"] == "click" \
            and a["target"] == val, (phrase, a)


def test_go_to_does_not_hijack_real_routes():
    # System actions and known web destinations still win; conversational
    # "go to bed" isn't a click.
    assert server.detect_action_fast("open gmail")["action"] == "open_url"
    assert server.detect_action_fast("go to sleep")["action"] == "system_action"
    b = server.detect_action_fast("go to bed")
    assert b is None or b["action"] != "ui_act", b


def test_send_to_claude_code_routes():
    # "send this to Claude Code to fix" / "have Claude Code fix this" → dispatch.
    for phrase in ("send this to claude code for fixing now",
                   "send this to claude code to fix", "have claude code fix this",
                   "get claude code to fix this", "fix this with claude code",
                   "dispatch this to claude to fix it", "send it to claude"):
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "send_to_claude_code", (phrase, a)


def test_field_dictation_and_repo_hint():
    # "dictate into here" → live field dictation; repo hint is extracted.
    for phrase in ("dictate into here", "type into this", "let me dictate",
                   "start dictating", "type what I say"):
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "start_field_dictation", (phrase, a)
    a = server.detect_action_fast("send this to claude code in the valet repo")
    assert a["action"] == "send_to_claude_code" and a["repo"] == "valet", a
    b = server.detect_action_fast("send this to claude code to fix")
    assert b["action"] == "send_to_claude_code" and b["repo"] == "", b


def test_logout_login_routes_to_ui_loop():
    # Log-in flows and non-Google logouts → the supervised UI loop (not a brittle
    # AppleScript). Google sign-OUT is the one exception (deterministic URL).
    for phrase in ("log back into gmail", "log into stripe", "sign in to stripe",
                   "log back in", "log out of stripe"):
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "ui_task" and a.get("goal"), (phrase, a)
    # Google/Gmail sign-OUT → deterministic in-place logout, not the loop. Gmail
    # carries a continue= so re-login returns to Gmail.
    a = server.detect_action_fast("sign out of gmail")
    assert a and a["action"] == "google_signout" and a["gmail"] is True, a
    a = server.detect_action_fast("log out of google")
    assert a and a["action"] == "google_signout" and a["gmail"] is False, a
    # Must not hijack lookalikes.
    for phrase in ("open gmail", "logo design ideas", "log my workout"):
        a = server.detect_action_fast(phrase)
        assert a is None or a["action"] not in ("ui_task", "google_signout"), (phrase, a)


def test_go_back_clicks_back_button():
    # "go back" → click the on-screen back button (e.g. Gmail's back-to-inbox).
    for phrase in ("go back", "go back to my inbox", "take me back", "head back"):
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "ui_act" and a["target"] == "back button", (phrase, a)
    # "go back to sleep" must not become a click.
    a = server.detect_action_fast("go back to sleep")
    assert a is None or a["target"] != "back button", a


def test_stripe_speech_correction():
    from voice_text import apply_speech_corrections as fix
    assert fix("open the strip email") == "open the Stripe email"
    assert fix("go to the strike dashboard") == "go to the Stripe dashboard"
    # General speech must be untouched.
    assert fix("strike that") == "strike that"
    assert fix("strip the trailing slash") == "strip the trailing slash"


def test_send_to_claude_code_does_not_hijack():
    # "open claude" is the terminal; a bare "what does claude think" is convo.
    assert server.detect_action_fast("open claude")["action"] == "open_terminal"
    for phrase in ("summarize this email", "what does claude think"):
        a = server.detect_action_fast(phrase)
        assert a is None or a["action"] != "send_to_claude_code", (phrase, a)


def test_summarize_does_not_hijack_describe_or_research():
    # "what's on my screen" stays describe; an arbitrary "summarize the <topic>"
    # is NOT a screen summary — it falls through to the LLM (research vs screen).
    assert server.detect_action_fast("what's on my screen")["action"] == "describe_screen"
    for phrase in ("summarize the news about openai", "summarize my q3 fishing trip plans"):
        a = server.detect_action_fast(phrase)
        assert a is None or a["action"] != "summarize_screen", (phrase, a)


def test_goto_account_email_and_summarize_keeps_account():
    # "Go to my work email and summarize today's emails" — the account word must
    # survive to the digest (issue #285 follow-up: the LLM dropped it, so the
    # digest read the wrong tab and said "nothing new"). Must NOT go to the LLM.
    for phrase, acct, when in [
        ("go to my work email and summarize today's emails", "work", "today"),
        ("open my personal inbox and catch me up", "personal", ""),
        ("go to my work email and summarize yesterday's emails", "work", "yesterday"),
        ("switch to my business gmail and go through today's mail", "business", "today"),
    ]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "summarize_inbox", (phrase, a)
        assert a["account"] == acct and a["date"] == when, (phrase, a)


def test_goto_account_digest_does_not_hijack_login_or_nav():
    # Generic "go to gmail and …" (no account) stays the login-capable ui_task,
    # and a bare "go to my work email" (no digest verb) is NOT a digest.
    a = server.detect_action_fast("go to gmail and summarize today's emails")
    assert a and a["action"] == "ui_task", a
    b = server.detect_action_fast("go to my work email")
    assert b is None or b["action"] != "summarize_inbox", b


class _FakeWS:
    """Minimal stand-in carrying a held summary for the router."""
    def __init__(self, last_summary=None):
        self.last_summary = last_summary


def test_save_summary_note_routes_with_fresh_summary():
    # "Put that summary in a note" (issue #286) — the cross-turn handoff. Claimed
    # only when a fresh summary is held on the session.
    import time
    ws = _FakeWS({"text": "Three emails today.", "title": "Gmail summary - today",
                  "ts": time.time()})
    for phrase in ("Ok, put that summary in a new apple note",
                   "put that in a note", "save that summary as a note",
                   "save that as a note", "note that down", "pop that in a note"):
        a = server.detect_action_fast(phrase, ws=ws)
        assert a and a["action"] == "save_summary_note", (phrase, a)


def test_save_summary_note_falls_through_without_summary():
    # No held summary → must NOT claim it (falls to the LLM's CREATE_NOTE path).
    ws = _FakeWS(None)
    a = server.detect_action_fast("save that as a note", ws=ws)
    assert a is None or a["action"] != "save_summary_note", a
    # None ws (no session) also can't route here.
    b = server.detect_action_fast("put that in a note", ws=None)
    assert b is None or b["action"] != "save_summary_note", b


def test_save_summary_note_falls_through_when_stale():
    # A summary older than the TTL is not resurrected by "put that in a note".
    ws = _FakeWS({"text": "old digest", "title": "x",
                  "ts": 0})  # ts=0 → decades stale
    a = server.detect_action_fast("put that in a note", ws=ws)
    assert a is None or a["action"] != "save_summary_note", a


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


def test_big_consumer_sites_open_as_web():
    # "open facebook / instagram / youtube" → open the website (no app/project).
    for phrase, host in [("open facebook", "facebook.com"),
                         ("open instagram", "instagram.com"),
                         ("open youtube", "youtube.com"),
                         ("open netflix", "netflix.com")]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "open_url" and host in a["target"], (phrase, a)


def test_browser_tabs_and_claude_in_chrome():
    # Tab control via keyboard shortcut; "open a new tab" is NOT web-searched.
    for phrase, combo in [("open a new tab", "cmd+t"), ("new tab", "cmd+t"),
                          ("close this tab", "cmd+w"), ("reopen the closed tab", "cmd+shift+t")]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "browser_tab" and a["combo"] == combo, (phrase, a)
    # "open Claude in chrome" = the web app, not the Claude Code terminal.
    a = server.detect_action_fast("open claude in chrome")
    assert a and a["action"] == "open_url" and "claude.ai" in a["target"], a
    # bare "open claude" still opens the terminal.
    assert server.detect_action_fast("open claude")["action"] == "open_terminal"


def test_timers_and_stopwatch():
    # Timer uses VALET's built-in announce-when-done timer (the Clock Timers tab
    # has no automatable duration field). Stopwatch/tabs/alarms DO use the Clock app.
    a = server.detect_action_fast("set a timer for three minutes")
    assert a and a["action"] == "set_timer" and a["seconds"] == 180, a
    # Stopwatch — clicks the named Clock-app button (vision mis-clicked the screen).
    a = server.detect_action_fast("start a stopwatch")
    assert a and a["action"] == "clock_stopwatch" and a["mode"] == "start", a
    a = server.detect_action_fast("stop the stopwatch")
    assert a and a["action"] == "clock_stopwatch" and a["mode"] == "stop", a


def test_any_stopwatch_phrasing_uses_apple_clock():
    # Every stopwatch phrasing must hit the Apple Clock app, never the web-search
    # fallback (online-stopwatch.com). Pause/stop words → stop mode.
    for phrase, mode in [("open a stopwatch", "start"), ("i need a stopwatch", "start"),
                         ("use the stopwatch", "start"),
                         ("pause the stopwatch", "stop"), ("reset the stopwatch", "stop")]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "clock_stopwatch" and a["mode"] == mode, (phrase, a)


def test_clock_tabs_and_alarms():
    for phrase, tab in [("go to the timers tab", "timers"),
                        ("switch to alarms in clock", "alarms"),
                        ("switch to the stopwatch tab", "stopwatch")]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "clock_tab" and a["tab"].replace(" ", "") == tab, (phrase, a)
    a = server.detect_action_fast("set an alarm for 7am")
    assert a and a["action"] == "clock_alarm" and a["hour"] == 7 and a["minute"] == 0, a
    a = server.detect_action_fast("set an alarm for 6:30 pm")
    assert a and a["action"] == "clock_alarm" and a["hour"] == 18 and a["minute"] == 30, a
    a = server.detect_action_fast("wake me at 6:30")
    assert a and a["action"] == "clock_alarm" and a["hour"] == 6 and a["minute"] == 30, a


def test_slack_compose_routes():
    for phrase, target, body in [
        ("slack Kuba saying the build is green", "Kuba", "the build is green"),
        ("dm Sarah on slack saying hi there", "Sarah", "hi there"),
        ("slack the design channel: assets are ready", "the design channel", "assets are ready"),
    ]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "compose_slack", (phrase, a)
        assert a["target"] == target and a["body"] == body, (phrase, a)


def test_copy_file_to_clipboard_routes():
    # "copy <file> to clipboard" → find_file with the clipboard flag (copy, not
    # open); the "(and) copy it to clipboard" tail is stripped from the query.
    for phrase, q in [
        ("find the juniper logo on my desktop and copy it to clipboard", "juniper logo"),
        ("copy my q2 report to the clipboard", "q2 report"),
        ("copy the juniper logo to clipboard", "juniper logo"),
    ]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "find_file" and a.get("clipboard") is True, (phrase, a)
        assert a["query"] == q, (phrase, a)
    # A plain find without the clipboard tail must NOT set the flag.
    a = server.detect_action_fast("find my q2 report")
    assert a and a["action"] == "find_file" and not a.get("clipboard"), a


def test_file_search_strips_location():
    import file_index
    for q, want in [("the juniper logo on my desktop", "juniper logo"),
                    ("my q2 report in downloads", "q2 report")]:
        _, name, _ = file_index.detect_kind(q)
        assert name == want, (q, name)


def test_text_contact_composes_addressed_not_blind_send():
    # "text/message <person> saying <body>" routes to compose_text (addresses the
    # NAMED contact) — never the old blind SEND into the focused thread that texted
    # the wrong person. Body keeps its original casing; multi-word names split.
    for phrase, recip, body in [
        ("text Camille saying I'll be late", "Camille", "I'll be late"),
        ("message my brother saying Happy Birthday", "my brother", "Happy Birthday"),
        ("text Camille: test", "Camille", "test"),
        ("shoot a text to Sarah saying on my way", "Sarah", "on my way"),
    ]:
        a = server.detect_action_fast(phrase)
        assert a and a["action"] == "compose_text", (phrase, a)
        assert a["recipient"] == recip and a["body"] == body, (phrase, a)
    # Bare form (no separator) is too ambiguous to split deterministically — it must
    # NOT fast-path; it falls through to the LLM (COMPOSE_TEXT). And never SEND.
    assert server.detect_action_fast("text Camille hi") is None
    # Unrelated "message me…" phrasing isn't a compose.
    a = server.detect_action_fast("message me the details")
    assert a is None or a["action"] != "compose_text", a
