"""Google OAuth + API client helper for JARVIS.

Handles loading the credentials.json, kicking off the local-server OAuth flow,
persisting tokens to data/google_tokens.json, refreshing them when expired,
and building authenticated Gmail / Calendar API clients.

Read-only by design — scopes cover reading mail and calendar, nothing more.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger("jarvis.google")

# Gmail: readonly for searching + reading, compose for draft creation. The
# code never calls messages.send() — drafts only, the user clicks Send.
# Calendar: full event read/write so JARVIS can schedule and cancel.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    # OpenID profile so we can show "connected as foo@gmail.com" in the UI.
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

_ROOT = Path(__file__).parent
CREDENTIALS_PATH = _ROOT / "google_credentials.json"
TOKEN_PATH = _ROOT / "data" / "google_tokens.json"

_cached_creds: Credentials | None = None
_cached_email: str | None = None


def credentials_file_exists() -> bool:
    return CREDENTIALS_PATH.exists()


def _load_creds() -> Credentials | None:
    """Load tokens from disk and refresh if expired. Returns None if no tokens yet."""
    global _cached_creds
    if _cached_creds and _cached_creds.valid:
        return _cached_creds
    if not TOKEN_PATH.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except Exception as e:
        log.warning(f"Failed to load tokens: {e}")
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_creds(creds)
        except Exception as e:
            log.warning(f"Token refresh failed: {e}")
            return None

    if creds and creds.valid:
        _cached_creds = creds
        return creds
    return None


def _save_creds(creds: Credentials) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())


def is_connected() -> bool:
    """True if we have valid (or refreshable) tokens on disk."""
    return _load_creds() is not None


def disconnect() -> None:
    """Forget stored tokens. credentials.json is left alone."""
    global _cached_creds, _cached_email
    _cached_creds = None
    _cached_email = None
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()


def get_connected_email() -> str | None:
    """Return the connected Google account's email address, or None if not connected."""
    global _cached_email
    if _cached_email:
        return _cached_email
    creds = _load_creds()
    if not creds:
        return None
    try:
        oauth2 = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        info = oauth2.userinfo().get().execute()
        _cached_email = info.get("email")
        return _cached_email
    except HttpError as e:
        log.warning(f"Failed to fetch userinfo: {e}")
        return None


def gmail_service():
    """Return an authenticated Gmail API client, or None if not connected."""
    creds = _load_creds()
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def calendar_service():
    """Return an authenticated Google Calendar API client, or None if not connected."""
    creds = _load_creds()
    if not creds:
        return None
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _run_oauth_flow_blocking() -> tuple[bool, str]:
    """Blocking OAuth flow: spawn a local server, open the browser, wait for callback.

    Returns (success, message_or_email).
    """
    if not CREDENTIALS_PATH.exists():
        return False, f"Missing {CREDENTIALS_PATH.name} — download OAuth client JSON from Google Cloud Console and place it at the project root."

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        # port=0 picks an open port; loopback IP per Google's OAuth Desktop spec.
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            prompt="consent",
            authorization_prompt_message="JARVIS is opening your browser to connect your Google account...",
            success_message="JARVIS connected. You can close this tab.",
        )
    except Exception as e:
        log.error(f"OAuth flow failed: {e}")
        return False, str(e)

    _save_creds(creds)
    global _cached_creds, _cached_email
    _cached_creds = creds
    _cached_email = None  # invalidate so get_connected_email() refetches

    email = get_connected_email() or "(unknown)"
    return True, email


async def connect_async() -> tuple[bool, str]:
    """Async wrapper — runs the blocking OAuth flow in a thread."""
    return await asyncio.to_thread(_run_oauth_flow_blocking)
