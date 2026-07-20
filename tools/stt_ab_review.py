#!/usr/bin/env python3
"""Adjudicate the Deepgram-vs-WebKit transcript pairs collected for issue #321.

Why this exists
---------------
#320 added Deepgram Nova for the push-to-talk turn on the theory that it is
materially better for accented English. That was never verified, and #321 wants
to build real spend-gating machinery on top of it. Building gating for a
recognizer that isn't actually better would be wasted work, so: measure first.

The measurement is nearly free because both recognizers ALREADY run on the same
audio for every hold (see `frontend/src/wakeWord.ts`) — Deepgram's transcript
simply overwrites WebKit's. `sttCompare.ts` records the pair instead.

The part that needs a human
--------------------------
A disagreement rate is not an accuracy rate. Knowing the two transcripts differ
says nothing about which was RIGHT — and only the person who spoke knows that.
So this tool shows each substantive disagreement and asks. Everything else it
can compute on its own.

Usage
-----
    python3 tools/stt_ab_review.py            # summary only, no prompting
    python3 tools/stt_ab_review.py --review   # adjudicate the disagreements
    python3 tools/stt_ab_review.py --verdict  # summary + tally of adjudications
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "stt_ab.jsonl"
VERDICTS = ROOT / "data" / "stt_ab_verdicts.jsonl"

# Deepgram runs smart_format + punctuate; WebKit does not. Comparing raw strings
# would flag a disagreement on nearly every turn and bury the real substitutions
# ("GitHub" -> "ghetto") this exists to find. Must stay in step with
# `isSubstantiveDisagreement` in frontend/src/sttCompare.ts.
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"\s+", " ", _PUNCT.sub(" ", s)).strip()


def substantive(a: str, b: str) -> bool:
    return normalize(a) != normalize(b)


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a torn final line is not worth failing over
    return out


def summarize(rows: list[dict]) -> dict:
    """Everything derivable WITHOUT a human judgement."""
    ran = [r for r in rows if r.get("deepgram_ran")]
    both = [r for r in ran if r.get("webkit") and r.get("deepgram")]
    disagree = [r for r in both if substantive(r["webkit"], r["deepgram"])]
    return {
        "samples": len(rows),
        "deepgram_ran": len(ran),
        # One side blank is a capture failure, not an accuracy result — a
        # recognizer that returns nothing loses the turn outright.
        "webkit_only": len([r for r in ran if r.get("webkit") and not r.get("deepgram")]),
        "deepgram_only": len([r for r in ran if r.get("deepgram") and not r.get("webkit")]),
        "both_spoke": len(both),
        "disagreements": len(disagree),
        "audio_minutes": round(sum(r.get("held_ms", 0) for r in ran) / 60000.0, 2),
    }


def print_summary(rows: list[dict]) -> None:
    s = summarize(rows)
    if not s["samples"]:
        print(f"No samples yet. Expected at: {SAMPLES}")
        print("Use push-to-talk (hold ⌃⌥) normally for a few days, then re-run.")
        return

    print(f"\n  Samples                {s['samples']}")
    print(f"  Deepgram ran           {s['deepgram_ran']}")
    print(f"  Both produced text     {s['both_spoke']}")
    print(f"  WebKit only            {s['webkit_only']}   (Deepgram returned nothing)")
    print(f"  Deepgram only          {s['deepgram_only']}   (WebKit returned nothing)")
    if s["both_spoke"]:
        pct = 100.0 * s["disagreements"] / s["both_spoke"]
        print(f"  Substantive disagree   {s['disagreements']}  ({pct:.0f}% of shared turns)")
    print(f"  Audio metered          {s['audio_minutes']} min "
          f"(~${s['audio_minutes'] * 0.0043:.3f} at Deepgram list)")

    if s["both_spoke"] and not s["disagreements"]:
        print("\n  The two recognizers never substantively disagreed.")
        print("  That is itself the answer: Deepgram is not buying accuracy here,")
        print("  and #321's gating build cannot be justified on quality grounds.")
    elif s["disagreements"]:
        print(f"\n  {s['disagreements']} disagreements need a human verdict —")
        print("  a difference is not an improvement. Run with --review.")
    print()


def review(rows: list[dict]) -> None:
    """Ask which transcript was right, one disagreement at a time."""
    done = {v["key"] for v in load(VERDICTS)}
    pending = [
        r for r in rows
        if r.get("deepgram_ran") and r.get("webkit") and r.get("deepgram")
        and substantive(r["webkit"], r["deepgram"])
        and f"{r.get('ts')}" not in done
    ]
    if not pending:
        print("Nothing left to adjudicate.")
        return

    print(f"\n{len(pending)} disagreement(s) to judge. You spoke these — which was right?")
    print("  [w] WebKit   [d] Deepgram   [n] neither   [s] skip   [q] quit\n")

    with open(VERDICTS, "a") as fh:
        for i, r in enumerate(pending, 1):
            print(f"── {i}/{len(pending)}  {r.get('date', '')}")
            print(f"   WebKit    {r['webkit']}")
            print(f"   Deepgram  {r['deepgram']}")
            try:
                ans = input("   > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nStopped. Progress saved.")
                return
            if ans == "q":
                print("Stopped. Progress saved.")
                return
            if ans == "s" or ans not in {"w", "d", "n"}:
                print()
                continue
            fh.write(json.dumps({
                "key": f"{r.get('ts')}",
                "winner": {"w": "webkit", "d": "deepgram", "n": "neither"}[ans],
                "webkit": r["webkit"],
                "deepgram": r["deepgram"],
            }) + "\n")
            fh.flush()
            print()
    print("Done. Run with --verdict for the tally.")


def verdict(rows: list[dict]) -> None:
    vs = load(VERDICTS)
    if not vs:
        print("No adjudications yet — run with --review first.")
        return
    tally = {"webkit": 0, "deepgram": 0, "neither": 0}
    for v in vs:
        tally[v.get("winner", "neither")] = tally.get(v.get("winner", "neither"), 0) + 1
    judged = len(vs)
    print(f"\n  Judged disagreements   {judged}")
    print(f"  Deepgram right         {tally['deepgram']}  ({100*tally['deepgram']/judged:.0f}%)")
    print(f"  WebKit right           {tally['webkit']}  ({100*tally['webkit']/judged:.0f}%)")
    print(f"  Neither                {tally['neither']}  ({100*tally['neither']/judged:.0f}%)")

    # A small sample can't distinguish a real effect from noise. Say so rather
    # than letting a 3-vs-1 split read as a decision.
    if judged < 20:
        print(f"\n  {judged} judged samples is too few to decide on. Keep using")
        print("  push-to-talk and re-run; aim for 20+ disagreements.")
        print()
        return

    net = tally["deepgram"] - tally["webkit"]
    print()
    if net > judged * 0.2:
        print("  Deepgram wins clearly. The #321 gating build is justified.")
    elif net < -judged * 0.2:
        print("  WebKit wins. Drop Deepgram rather than building gating for it.")
    else:
        print("  No clear winner. Deepgram is not paying for the gating build —")
        print("  keep the built-in recognizer and close #321 as not-worth-it.")
    print()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--review", action="store_true", help="adjudicate disagreements")
    p.add_argument("--verdict", action="store_true", help="tally adjudications")
    args = p.parse_args()

    rows = load(SAMPLES)
    print_summary(rows)
    if args.review:
        review(rows)
    if args.verdict:
        verdict(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
