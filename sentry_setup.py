"""Opt-in error reporting (Stage F).

OFF by default. Enabled only when BOTH hold:
  - the user consented (VALET_TELEMETRY=on, set from Settings), and
  - a SENTRY_DSN is configured.

Because VALET can see files, messages, and screen context, payloads are
scrubbed hard: we send error *metadata* (exception type, module, the action
that failed) but never file contents, message bodies, prompts, request data, or
breadcrumbs. No-op (never raises) if sentry-sdk isn't installed.
"""

import logging
import os

log = logging.getLogger("valet.telemetry")


def telemetry_enabled() -> bool:
    """On by default (crash reports help us fix what breaks); the user can opt out
    in Settings. Still requires a SENTRY_DSN to actually send anything."""
    consent = os.getenv("VALET_TELEMETRY", "on").strip().lower() not in ("0", "off", "false", "no")
    return consent and bool(os.getenv("SENTRY_DSN", "").strip())


def _scrub(event, hint):
    """Strip anything that could carry user content before it leaves the machine."""
    for key in ("request", "extra", "breadcrumbs", "contexts", "user"):
        event.pop(key, None)
    # Keep only exception type + where it happened; drop any value strings that
    # might echo a file path, prompt, or message body.
    for exc in (event.get("exception", {}) or {}).get("values", []) or []:
        exc.pop("stacktrace", None)
        if "value" in exc:
            exc["value"] = "(scrubbed)"
    return event


def setup_telemetry() -> None:
    """Initialize Sentry iff consented + configured. Safe to call always."""
    if not telemetry_enabled():
        return
    try:
        import sentry_sdk
    except ImportError:
        log.info("telemetry consented but sentry-sdk not installed — skipping")
        return
    try:
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            send_default_pii=False,
            before_send=_scrub,
            traces_sample_rate=0.0,
            release=os.getenv("VALET_RELEASE", "valet@0.1.0"),
        )
        log.info("telemetry enabled (Sentry, payloads scrubbed)")
    except Exception as e:
        log.warning(f"telemetry init failed: {e}")


def capture_action_error(action: str, error: Exception) -> None:
    """Report a failed action with metadata only (no targets/contents)."""
    if not telemetry_enabled():
        return
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("action", action)
            sentry_sdk.capture_exception(error)
    except Exception:
        pass
