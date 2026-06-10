"""Langfuse observability for VALET.

Adds tracing to every Anthropic LLM call in the app via OpenTelemetry
auto-instrumentation (the approach Langfuse recommends for the raw Anthropic
Python SDK — see https://langfuse.com/integrations/model-providers/anthropic).
One `instrument()` call captures model, token usage, latency, and input/output
for every `client.messages.create(...)` / `.stream(...)` across server.py,
planner.py, and screen.py — no per-call-site changes needed.

Design notes:
- **Safe to import always.** All third-party imports are lazy (inside
  `setup_observability`), so importing this module never fails even if the
  Langfuse / OpenTelemetry packages aren't installed yet.
- **Graceful no-op.** If the LANGFUSE_* credentials aren't set (or the packages
  are missing), setup logs once and the app runs exactly as before — tracing is
  simply off. Nothing here is allowed to break the voice loop.
- **PII masking.** A conservative `_mask` redactor strips emails and long digit
  runs from traced input/output before anything leaves the machine. VALET reads
  Mail/Notes/Calendar, so this matters.

Call `setup_observability()` once at startup (before the first LLM call) and
`shutdown_observability()` on exit to flush buffered traces.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("valet.observability")

# Module-level state. `_client` is the Langfuse singleton once configured.
_client = None
_enabled = False
_setup_done = False

# Redaction patterns applied to all traced input/output.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 7+ consecutive digits (phone numbers, account/card-ish numbers). Deliberately
# conservative — short numbers (years, counts, small IDs) are left alone.
_LONG_DIGITS_RE = re.compile(r"\b\d[\d\s-]{6,}\d\b")


def _redact_text(text: str) -> str:
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _LONG_DIGITS_RE.sub("[REDACTED_NUMBER]", text)
    return text


def _mask(data):
    """Langfuse mask callback. Recursively redact PII from traced data.

    Receives the input/output payload Langfuse is about to send and returns a
    sanitized copy. Must never raise — on any error we drop to a safe placeholder
    rather than risk leaking the unmasked value.
    """
    try:
        if isinstance(data, str):
            return _redact_text(data)
        if isinstance(data, dict):
            return {k: _mask(v) for k, v in data.items()}
        if isinstance(data, (list, tuple)):
            return type(data)(_mask(v) for v in data)
        return data
    except Exception:
        return "[REDACTED]"


def is_enabled() -> bool:
    return _enabled


def setup_observability() -> bool:
    """Initialize Langfuse + Anthropic auto-instrumentation. Idempotent.

    Returns True if tracing is active, False if it's a no-op (missing creds or
    packages). Safe to call unconditionally at startup.
    """
    global _client, _enabled, _setup_done
    if _setup_done:
        return _enabled
    _setup_done = True

    # The SDK reads LANGFUSE_HOST; accept LANGFUSE_BASE_URL as an alias (the
    # Langfuse docs/onboarding use both names interchangeably).
    if not os.getenv("LANGFUSE_HOST") and os.getenv("LANGFUSE_BASE_URL"):
        os.environ["LANGFUSE_HOST"] = os.environ["LANGFUSE_BASE_URL"]

    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        log.info("Langfuse not configured (LANGFUSE_PUBLIC_KEY/SECRET_KEY unset) — tracing disabled")
        return False

    try:
        from langfuse import Langfuse
        from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
    except ImportError as e:
        log.warning(
            "Langfuse packages not installed (%s) — tracing disabled. "
            "Run: pip install -r requirements.txt", e,
        )
        return False

    try:
        # Initializes the singleton from LANGFUSE_* env vars; mask scrubs PII.
        _client = Langfuse(mask=_mask)
        if not _client.auth_check():
            log.warning("Langfuse auth_check failed — check keys/host. Tracing disabled.")
            _client = None
            return False
        # Patch the Anthropic SDK so every messages.create/.stream is traced.
        AnthropicInstrumentor().instrument()
        _enabled = True
        log.info("Langfuse tracing enabled (host=%s)", os.getenv("LANGFUSE_HOST", "default"))
        return True
    except Exception as e:
        log.warning("Langfuse setup failed (%s) — tracing disabled", e)
        _client = None
        _enabled = False
        return False


def observe(**kwargs):
    """Safe `@observe` decorator factory for naming a feature span.

    Use as `@observe(name="classify-intent", capture_input=False)`. The decorated
    function's auto-instrumented Anthropic calls nest under this named span, and
    any session_id/user_id from an active `connection_scope` propagate to it.

    If Langfuse isn't installed, this is a transparent no-op (returns the function
    unchanged). Decoration happens at import time; the span is only created — and
    only exported — at call time once tracing is configured.
    """
    def decorator(fn):
        # Gate on credentials so that, when tracing is off, this is a true no-op:
        # no Langfuse client is created and no "client disabled" warning fires.
        # server.py loads .env before these decorators run, so the keys are in the
        # environment by decoration time if they're configured at all.
        if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
            return fn
        try:
            from langfuse import observe as _observe
        except ImportError:
            return fn
        return _observe(**kwargs)(fn)
    return decorator


class ConnectionScope:
    """Manually-managed scope that stamps `session_id`/`user_id`/`tags` onto every
    trace created while it's open. `enter()` and `close()` never raise, so it can
    wrap a long-lived handler via try/finally without risk to the request loop.

    Used per WebSocket connection so all of a conversation's traces group together
    in the Langfuse Sessions view.
    """

    def __init__(self, session_id, user_id=None, tags=None, metadata=None):
        self._args = (session_id, user_id, tags, metadata)
        self._cm = None

    def enter(self):
        if not _enabled:
            return self
        try:
            from langfuse import propagate_attributes
            sid, uid, tags, meta = self._args
            kwargs = {"session_id": sid}
            if uid:
                kwargs["user_id"] = uid
            if tags:
                kwargs["tags"] = tags
            if meta:
                kwargs["metadata"] = meta
            self._cm = propagate_attributes(**kwargs)
            self._cm.__enter__()
        except Exception as e:  # never let tracing break the connection
            log.debug("connection scope enter failed: %s", e)
            self._cm = None
        return self

    def close(self):
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                pass
            self._cm = None


def connection_scope(session_id, user_id=None, tags=None, metadata=None) -> "ConnectionScope":
    """Create a per-connection trace scope. Call `.enter()` to start it and
    `.close()` (in a finally) to end it. Safe no-op when tracing is disabled."""
    return ConnectionScope(session_id, user_id=user_id, tags=tags, metadata=metadata)


def shutdown_observability() -> None:
    """Flush buffered traces on shutdown so nothing is lost. Never raises.

    While running, the OTel BatchSpanProcessor exports on a ~5s timer, so live
    traces appear on their own. On a clean shutdown we additionally force-flush
    both Langfuse's queues and the OTel span processor so the final few spans
    aren't dropped when the process exits before the next timer tick.
    """
    if _client is None:
        return
    try:
        _client.flush()
    except Exception as e:
        log.debug("Langfuse flush on shutdown failed: %s", e)
    try:
        from opentelemetry import trace
        trace.get_tracer_provider().force_flush()
    except Exception as e:
        log.debug("OTel force_flush on shutdown failed: %s", e)
