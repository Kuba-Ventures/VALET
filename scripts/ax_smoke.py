#!/usr/bin/env python3
"""UC1 live smoke test — exercise the Accessibility primitives on a real Mac.

Builds the real executor stack (AppleScript → Accessibility composite, wrapped in
a SafeExecutor with an auto-allow confirmation stub) and runs the universal
control primitives against a live app. Requires a GUI session and the
**Accessibility** permission granted to whatever runs this (Terminal / the app).

  # Read the focused window's element tree (safe, read-only):
  ./.venv/bin/python scripts/ax_smoke.py observe
  ./.venv/bin/python scripts/ax_smoke.py observe --app "System Settings"

  # One-shot acceptance chain (observe → click the first text field → type →
  # re-observe and read it back), all in one process so refs resolve. Open a
  # blank document in the target app first. Synthesizes input:
  ./.venv/bin/python scripts/ax_smoke.py demo --app TextEdit --text "Hello from Vee."

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


def _client_from_env():
    """Build an Anthropic client from .env (dev key or license/proxy) for `act`."""
    import os
    from pathlib import Path
    for p in (Path(__file__).parent.parent / ".env",
              Path.home() / "Library/Application Support/VALET/.env"):
        try:
            if p.exists():
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass
    import anthropic
    if os.getenv("LICENSE_KEY"):
        base = os.getenv("PROXY_BASE_URL", "https://www.valet-voice.com")
        return anthropic.AsyncAnthropic(api_key="license-proxy", base_url=f"{base}/api/proxy",
                                        default_headers={"X-License-Key": os.environ["LICENSE_KEY"]}, timeout=30.0)
    if os.getenv("ANTHROPIC_API_KEY"):
        return anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=30.0)
    return None


async def _act(ex, app, target, intent):
    """UC3 resolution-only repro: show what a natural-language target resolves to
    (does NOT execute — pair with /api/ui/act in the running app to click/type)."""
    import perception, target_resolver
    client = _client_from_env()
    if client is None:
        print("No ANTHROPIC_API_KEY/LICENSE_KEY in env — can't resolve."); return
    obs = await perception.build_observation(ex, app=app)
    r = await target_resolver.resolve(obs, target, client, intent=intent)
    print(f"target {target!r} -> status={r.status} via={r.via} ref={r.ref} point={r.point} label={r.label!r}")
    if r.status == "ambiguous":
        print("  alternatives:", r.alternatives)
    if r.message:
        print("  message:", r.message)


async def _screen(ex, app):
    """UC2 observation: focused-window screenshot + AX snapshot summary."""
    import perception
    print("screen_recording_trusted:", perception.screen_recording_trusted())
    obs = await perception.build_observation(ex, app=app)
    img = obs["image"]
    print(f"app: {obs['app']} | ax_ok: {obs['ax_ok']} | elements: {len(obs['elements'])}")
    print(f"window_frame: {obs['window_frame']}")
    print("image:", f"{img['width']}x{img['height']} {img['media_type']} (~{len(img['b64'])*3//4//1024} KB)"
          if img else "None (Screen Recording not granted, or no window)")
    print("--- elements ---")
    print(perception.elements_as_text(obs["elements"], limit=15))


async def _demo(ex, app, text):
    """One-process acceptance chain: observe → click the first text field → type
    → re-observe and read the field value back. Refs are snapshot-scoped to this
    process, so observe and click must share one run (the UC4 loop does too)."""
    obs = await ex.observe_ui(app=app)
    if not obs.ok:
        print(f"observe failed: {obs.error} — {obs.message}")
        return
    els = obs.data["elements"]
    print(f"1) observe: {len(els)} elements in {app}")
    field = next((e for e in els if e["role"] in ("AXTextArea", "AXTextField", "AXSearchField")), None)
    if not field:
        print("   no text field/area found to type into — observe-only result above.")
        return
    print(f"   target field: {field['ref']} {field['role']} value={field['value']!r}")
    clk = await ex.click_element(ref=field["ref"], app=app)
    print(f"2) click({field['ref']}): ok={clk.ok} method={clk.meta.get('method')} {clk.message}")
    await asyncio.sleep(0.4)
    typ = await ex.send_keystroke(app, text, press_enter=False)
    print(f"3) type {text!r}: ok={typ.ok} {typ.message}")
    await asyncio.sleep(0.4)
    obs2 = await ex.observe_ui(app=app)
    f2 = next((e for e in obs2.data["elements"] if e["ref"] == field["ref"]
               or e["role"] == field["role"]), None)
    print(f"4) re-observe field value = {f2['value']!r}" if f2 else "4) field gone on re-observe")


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
    pd = sub.add_parser("demo"); pd.add_argument("--app", default="TextEdit"); pd.add_argument("--text", default="Hello from Vee.")
    ps = sub.add_parser("screen"); ps.add_argument("--app", default=None)
    pa = sub.add_parser("act"); pa.add_argument("--app", default=None); pa.add_argument("--target", required=True); pa.add_argument("--intent", default="click")
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
    if args.cmd == "demo":
        await _demo(ex, args.app, args.text)
        return
    if args.cmd == "screen":
        await _screen(ex, args.app)
        return
    if args.cmd == "act":
        await _act(ex, args.app, args.target, args.intent)
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
