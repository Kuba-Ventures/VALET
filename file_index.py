"""Spotlight (mdfind) file/document resolver for the voice console (Stage 2).

"open my Q2 report", "find screenshots from yesterday" resolve to real files via
``mdfind`` and open with no LLM. ``rank_hits`` and ``detect_kind`` are pure and
unit-tested; ``find_files`` is the only thing that shells out, and it is
timeout-capped so a cold query can never block the voice hot path.

Search is scoped to the user's home (where docs/downloads/screenshots live) for
speed and relevance. A confident single hit opens directly; multiple hits return
a short list the caller disambiguates by voice.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path

# Spoken kind word → an mdfind metadata predicate. Lets "open my X pdf" or
# "find screenshots" narrow by type instead of guessing from the name.
_KINDS: dict[str, str] = {
    "pdf": 'kMDItemContentTypeTree == "com.adobe.pdf"',
    "screenshot": "kMDItemIsScreenCapture == 1",
    "image": 'kMDItemContentTypeTree == "public.image"',
    "spreadsheet": 'kMDItemContentTypeTree == "public.spreadsheet"',
    "presentation": 'kMDItemContentTypeTree == "public.presentation"',
    "movie": 'kMDItemContentTypeTree == "public.movie"',
}

# Spoken words → canonical kind key above.
_KIND_WORDS: dict[str, str] = {
    "pdf": "pdf", "pdfs": "pdf",
    "screenshot": "screenshot", "screenshots": "screenshot",
    "screen shot": "screenshot", "screen shots": "screenshot",
    "image": "image", "images": "image", "photo": "image", "photos": "image",
    "picture": "image", "pictures": "image",
    "spreadsheet": "spreadsheet", "spreadsheets": "spreadsheet", "excel": "spreadsheet",
    "presentation": "presentation", "presentations": "presentation",
    "keynote": "presentation", "powerpoint": "presentation", "slides": "presentation",
    "video": "movie", "videos": "movie", "movie": "movie", "movies": "movie",
}

# Recency cue words — the caller sorts kind/broad results newest-first when present.
_RECENCY_WORDS = ("recent", "latest", "today", "yesterday", "this week", "last")


@dataclass(frozen=True)
class FileHit:
    path: str
    name: str    # basename
    score: int


def detect_kind(text: str) -> tuple[str | None, str, bool]:
    """Split a spoken file query into (kind, cleaned_name_query, wants_recent).
    Strips kind + recency words so the remainder is the name to match on."""
    s = (text or "").strip().lower()
    wants_recent = any(w in s for w in _RECENCY_WORDS)
    kind: str | None = None
    # longest phrases first so "screen shot" wins over "shot"
    for word in sorted(_KIND_WORDS, key=len, reverse=True):
        if f" {word} " in f" {s} ":
            kind = _KIND_WORDS[word]
            s = f" {s} ".replace(f" {word} ", " ").strip()
            break
    for w in _RECENCY_WORDS:
        s = s.replace(w, " ")
    # drop leftover filler
    for filler in ("from", "my", "the", "a ", "of", "for"):
        s = f" {s} ".replace(f" {filler} ", " ").strip()
    return kind, " ".join(s.split()), wants_recent


def _score(path: str, query: str, home: str) -> int:
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0].lower()
    nl = name.lower()
    q = query.lower().strip()
    score = 10  # mdfind already matched something
    if q:
        if stem == q or nl == q:
            score = 100
        elif stem.startswith(q) or nl.startswith(q):
            score = 70
        elif q in nl:
            score = 50
        else:
            score = 10
    if path.startswith(home):
        score += 20
    if "/Library/" in path or path.startswith("/System"):
        score -= 40
    score -= path.count("/")  # shallower = more likely the thing you meant
    return score


def rank_hits(paths: list[str], query: str, *, home: str | None = None) -> list[FileHit]:
    """Pure ranking of mdfind result paths for a name query. Best first."""
    h = home or str(Path.home())
    hits = [FileHit(p, os.path.basename(p), _score(p, query, h)) for p in paths if p]
    hits.sort(key=lambda x: (-x.score, len(x.path)))
    return hits


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


async def find_files(
    query: str,
    *,
    kind: str | None = None,
    recent: bool = False,
    limit: int = 8,
    timeout: float = 2.0,
    home: str | None = None,
) -> list[FileHit]:
    """Resolve a spoken file query to ranked hits via mdfind (home-scoped,
    timeout-capped). Returns [] on no match / timeout — caller falls through."""
    h = home or str(Path.home())
    pred = _KINDS.get(kind or "")
    q = (query or "").strip()
    args = ["mdfind", "-onlyin", h]
    if pred and q:
        safe = q.replace('"', "")
        args.append(f'{pred} && kMDItemDisplayName == "*{safe}*"c')
    elif pred:
        args.append(pred)
    elif q:
        args += ["-name", q]
    else:
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return []
    except Exception:
        return []
    paths = [p for p in out.decode("utf-8", "replace").splitlines() if p][:300]
    if not paths:
        return []
    hits = rank_hits(paths, q, home=h)
    # For kind-driven / recency queries the name signal is weak — surface the
    # newest files first (bounded stat over the top candidates).
    if recent or not q:
        top = hits[: max(limit * 3, 24)]
        top.sort(key=lambda x: (-_mtime(x.path),))
        hits = top + hits[len(top):]
    return hits[:limit]
