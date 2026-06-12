#!/usr/bin/env python3
"""UC1 live smoke test — exercise the Accessibility primitives on a real Mac.

Builds the real executor stack (AppleScript → Accessibility composite, wrapped in
a SafeExecutor with an auto-allow confirmation stub) and runs the universal
control primitives against a live app. Requires a GUI session and the
**Accessibility** permission granted to whatever runs this (Terminal / the app).

  # Read the focused window's element tree (safe, read-only):
  ./.venv/bin/python scripts/ax_smoke.py observe
  ./.venv/bin/python scripts/ax_smoke.py observe --app "System Settings"

  # Synthesize input (MOVES THE MOUSE / TYPES — run deliberately, watch it):
  ./.venv/bin/python scripts/ax_smoke.py click --ref e3 --yes
  ./.venv/bin/python scripts/ax_smoke.py click --point 200,400 --yes
  ./.venv/bin/python scripts/ax_smoke.py type --app TextEdit --text "Hello from Vee" --yes
  ./.venv/bin/python scripts/ax_smoke.py key --app TextEdit --combo cmd+a --yes

Input actions require --yes so they can't fire by accident.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from applescript_executor import AppleScriptExecutor
from accessibility_executor import AccessibilityExecutor, is_trusted
from composite_executor import CompositeExecutor
from safe_executor import SafeExecutor


class _AutoConfirm:
    """Stands in for the WebSocket confirm card: prints the Tier-1 summary and
    auto-allows, so the smoke run exercises the gating path without a UI."""

    async def request(self, *, summary, detail="", targets=None, tier=1, warning=None, timeout=120.0):
        print(f"  [confirm card] {summary}  (tier {tier}) -> AUTO-ALLOW")
        return True


class _Kill:
    def is_engaged(self):
        return False


def _build_executor():
    base = CompositeExecutor(AppleScriptExecutor(), AccessibilityExecutor())
    return SafeExecutor(base, confirmations=_AutoConfirm(), kill_switch=_Kill())


async def _observe(ex, app):
    res = await ex.observe_ui(app=app)
    if not res.ok:
        print(f"observe failed: {res.error} — {res.message}")
        return
    els = res.data["elements"]
    print(f"{res.data['app']}: {len(els)} elements")
    for e in els:
        fr = e["frame"]
        frs = f"[{fr[0]:.0f},{fr[1]:.0f} {fr[2]:.0f}x{fr[3]:.0f}]" if fr else "—"
        label = e["title"] or e["value"] or ""
        print(f"  {e['ref']:>4}  {e['role']:<18} {frs:<22} {label[:50]}")


async def _main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    po = sub.add_parser("observe"); po.add_argument("--app", default=None)
    pc = sub.add_parser("click"); pc.add_argument("--ref"); pc.add_argument("--point"); pc.add_argument("--app"); pc.add_argument("--yes", action="store_true")
    pt = sub.add_parser("type"); pt.add_argument("--app", default=""); pt.add_argument("--text", required=True); pt.add_argument("--enter", action="store_true"); pt.add_argument("--yes", action="store_true")
    pk = sub.add_parser("key"); pk.add_argument("--combo", required=True); pk.add_argument("--app"); pk.add_argument("--yes", action="store_true")
    args = p.parse_args()

    print(f"AXIsProcessTrusted: {is_trusted()}")
    if not is_trusted():
        print("  → grant Accessibility to this process (System Settings → Privacy → "
              "Accessibility) or input/observe will fail cleanly.")
    ex = _build_executor()

    if args.cmd == "observe":
        await _observe(ex, args.app)
        return
    if not getattr(args, "yes", False):
        print("Refusing to synthesize input without --yes.")
        sys.exit(2)
    if args.cmd == "click":
        point = tuple(float(x) for x in args.point.split(",")) if args.point else None
        res = await ex.click_element(ref=args.ref, point=point, app=args.app)
    elif args.cmd == "type":
        res = await ex.send_keystroke(args.app, args.text, press_enter=args.enter)
    elif args.cmd == "key":
        res = await ex.key_combo(args.combo, app=args.app)
    print(f"-> ok={res.ok} {res.message} ({res.error or 'no error'})")


if __name__ == "__main__":
    asyncio.run(_main())
