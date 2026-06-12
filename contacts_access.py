"""Apple Contacts via the Contacts framework — read-only name → email lookup.

Uses the Contacts permission (a separate TCC grant from Calendar/Automation).
The fallback source for name resolution: VALET's own profile store
(memory.find_contact) is checked first, then this. All functions are defensive —
a missing framework or denied permission returns [] / an error, never raises.
"""

import asyncio
import logging
import threading

log = logging.getLogger("valet.contacts")

# CNAuthorizationStatus: 0 notDetermined, 1 restricted, 2 denied, 3 authorized.
_store = None


def _store_obj():
    global _store
    if _store is None:
        from Contacts import CNContactStore
        _store = CNContactStore.alloc().init()
    return _store


def auth_status() -> int:
    """Current Contacts authorization status, or -1 if the framework is missing."""
    try:
        from Contacts import CNContactStore, CNEntityTypeContacts
        return int(CNContactStore.authorizationStatusForEntityType_(CNEntityTypeContacts))
    except Exception as e:
        log.warning(f"contacts auth_status failed: {e}")
        return -1


def has_access() -> bool:
    return auth_status() == 3  # authorized


def request_access(timeout: float = 60) -> bool:
    """Request Contacts access — triggers the native prompt the first time.
    Blocks up to `timeout` for the user's response (handler runs on its own queue)."""
    try:
        from Contacts import CNEntityTypeContacts
        store = _store_obj()
        result = {"granted": False}
        done = threading.Event()

        def cb(granted, err):
            result["granted"] = bool(granted)
            done.set()

        store.requestAccessForEntityType_completionHandler_(CNEntityTypeContacts, cb)
        done.wait(timeout=timeout)
        return has_access() or result["granted"]
    except Exception as e:
        log.warning(f"contacts access request failed: {e}")
        return False


def _find_emails_blocking(name: str) -> list[dict]:
    if not has_access():
        return []
    try:
        from Contacts import (
            CNContact, CNContactEmailAddressesKey,
            CNContactGivenNameKey, CNContactFamilyNameKey,
        )
        store = _store_obj()
        pred = CNContact.predicateForContactsMatchingName_(name)
        keys = [CNContactGivenNameKey, CNContactFamilyNameKey, CNContactEmailAddressesKey]
        matches, _err = store.unifiedContactsMatchingPredicate_keysToFetch_error_(pred, keys, None)
        out = []
        for c in (matches or []):
            full = " ".join(
                x for x in [str(c.givenName() or ""), str(c.familyName() or "")] if x
            ).strip()
            for lv in (c.emailAddresses() or []):
                email = str(lv.value() or "")
                if "@" in email:
                    out.append({"name": full or name, "email": email})
        return out
    except Exception as e:
        log.warning(f"contacts find failed: {e}")
        return []


async def find_emails(name: str) -> list[dict]:
    """All {name,email} matches for a spoken name. [] without access / on error."""
    return await asyncio.to_thread(_find_emails_blocking, name)


async def find_one(name: str) -> dict | None:
    """A single UNAMBIGUOUS match (after de-duping by email), or None (0 or many).
    None means the caller should ask rather than guess between addresses."""
    matches = await find_emails(name)
    seen, uniq = set(), []
    for m in matches:
        key = m["email"].lower()
        if key not in seen:
            seen.add(key)
            uniq.append(m)
    return uniq[0] if len(uniq) == 1 else None
