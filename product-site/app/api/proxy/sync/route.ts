import { NextRequest, NextResponse } from "next/server";
import { authorizeLicense } from "@/lib/proxy/auth";
import { getSupabaseAdmin } from "@/lib/supabase";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 10;

/**
 * POST /api/proxy/sync
 * Header: X-License-Key: PRODUCT-....
 *
 * The desktop app pushes a small snapshot here so the account dashboard can
 * show the user their profile, speech stats and connected apps. This is the
 * ONLY thing the app sends up beyond metered AI/TTS calls.
 *
 * Privacy: we accept ONLY the known fields below and coerce their types — any
 * extra keys in the body are dropped. No raw prompts or message content are
 * stored; stats are aggregates the app already computes locally.
 *
 * Body (all optional):
 *   profile:     { name, honorific, date_of_birth, location, work_email, personal_email }
 *   stats:       { total_tasks, success_rate, avg_duration_seconds,
 *                  top_actions: [{action, count}],
 *                  top_apps: [{label, count}], top_sites: [{label, count}] }
 *   connections: { calendar, mail, notes }
 *   app_version: string
 */

function str(v: unknown, max = 200): string | null {
  if (typeof v !== "string") return null;
  const t = v.trim();
  return t ? t.slice(0, max) : null;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

// Max base64 length for an app icon — mirrors the desktop cap. A true-64px PNG
// icon is a few KB, so this comfortably fits real icons while rejecting junk.
const MAX_ICON_B64 = 40_000;
const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

/**
 * Accept a base64 string only if it decodes to something that actually starts
 * with the PNG magic bytes and stays under the size cap. Anything else — wrong
 * charset, oversized, not a PNG — is dropped. App icons are not user content
 * (identical for everyone with the app), but we still validate strictly so the
 * store only ever holds small, well-formed images.
 */
function sanitizeIconB64(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  if (!s || s.length > MAX_ICON_B64) return null;
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(s)) return null;
  let buf: Buffer;
  try {
    buf = Buffer.from(s, "base64");
  } catch {
    return null;
  }
  if (buf.length < 8 || PNG_SIGNATURE.some((b, i) => buf[i] !== b)) return null;
  return s;
}

function sanitizeProfile(p: unknown): Record<string, string> | null {
  if (!p || typeof p !== "object") return null;
  const src = p as Record<string, unknown>;
  const out: Record<string, string> = {};
  for (const key of [
    "name",
    "honorific",
    "date_of_birth",
    "location",
    "work_email",
    "personal_email",
  ]) {
    const v = str(src[key]);
    if (v) out[key] = v;
  }
  return Object.keys(out).length ? out : null;
}

function sanitizeStats(s: unknown): Record<string, unknown> | null {
  if (!s || typeof s !== "object") return null;
  const src = s as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  const total = num(src.total_tasks);
  if (total !== null) out.total_tasks = Math.max(0, Math.round(total));
  const rate = num(src.success_rate);
  if (rate !== null) out.success_rate = Math.max(0, Math.min(100, rate));
  const dur = num(src.avg_duration_seconds);
  if (dur !== null) out.avg_duration_seconds = Math.max(0, dur);
  if (Array.isArray(src.top_actions)) {
    out.top_actions = src.top_actions
      .map((a) => {
        const action = str((a as Record<string, unknown>)?.action, 60);
        const count = num((a as Record<string, unknown>)?.count);
        return action ? { action, count: count ? Math.round(count) : 0 } : null;
      })
      .filter(Boolean)
      .slice(0, 10);
  }
  // Per-target breakdowns: which apps / site domains people open. Same shape as
  // top_actions but keyed by `label` (app name or bare domain — no content).
  // top_apps items may also carry an `icon` (base64 PNG of the app's macOS
  // icon); it's validated here and later moved to the shared app_icons store.
  for (const key of ["top_apps", "top_sites"] as const) {
    if (Array.isArray(src[key])) {
      out[key] = (src[key] as unknown[])
        .map((a) => {
          const rec = a as Record<string, unknown>;
          const label = str(rec?.label, 80);
          if (!label) return null;
          const count = num(rec?.count);
          const item: { label: string; count: number; icon?: string } = {
            label,
            count: count ? Math.round(count) : 0,
          };
          if (key === "top_apps") {
            const icon = sanitizeIconB64(rec?.icon);
            if (icon) item.icon = icon;
          }
          return item;
        })
        .filter(Boolean)
        .slice(0, 10);
    }
  }
  return Object.keys(out).length ? out : null;
}

function sanitizeConnections(c: unknown): Record<string, boolean> | null {
  if (!c || typeof c !== "object") return null;
  const src = c as Record<string, unknown>;
  const out: Record<string, boolean> = {};
  for (const key of ["calendar", "mail", "notes"]) {
    const v = bool(src[key]);
    if (v !== null) out[key] = v;
  }
  return Object.keys(out).length ? out : null;
}

interface AppIconRow {
  slug: string;
  label: string;
  png_base64: string;
  updated_at: string;
}

/**
 * Pull icon bytes out of stats.top_apps into shared app_icons rows, mutating
 * stats so the per-license account_sync row never stores image bytes — they'd
 * bloat every row and the cross-user read in getUsageInsights. The slug
 * (normalised label) collapses the same app across users to one shared icon.
 */
function extractAppIcons(
  stats: Record<string, unknown> | null,
  updatedAt: string,
): AppIconRow[] {
  if (!stats || !Array.isArray(stats.top_apps)) return [];
  const rows: AppIconRow[] = [];
  for (const item of stats.top_apps as Record<string, unknown>[]) {
    const icon = typeof item.icon === "string" ? item.icon : null;
    const label = typeof item.label === "string" ? item.label : "";
    const slug = label.trim().toLowerCase();
    if (icon && slug) {
      rows.push({ slug, label, png_base64: icon, updated_at: updatedAt });
    }
    delete item.icon; // never persist image bytes on the per-license row
  }
  return rows;
}

export async function POST(req: NextRequest) {
  const auth = await authorizeLicense(req);
  if (!auth.ok) return auth.response;

  let body: Record<string, unknown> = {};
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const row: Record<string, unknown> = {
    license_key: auth.licenseKey,
    updated_at: new Date().toISOString(),
  };
  const profile = sanitizeProfile(body.profile);
  const stats = sanitizeStats(body.stats);
  const connections = sanitizeConnections(body.connections);
  const appVersion = str(body.app_version, 40);
  // Extract icon bytes BEFORE storing stats — this strips them from `stats` too.
  const appIcons = extractAppIcons(stats, row.updated_at as string);
  if (profile) row.profile = profile;
  if (stats) row.stats = stats;
  if (connections) row.connections = connections;
  if (appVersion) row.app_version = appVersion;

  try {
    const supabase = getSupabaseAdmin();
    // Shared, cross-user app-icon store. Best-effort and non-fatal: an icon
    // write must never fail the sync. First writer wins (ignoreDuplicates) so
    // we don't rewrite the same icon on every 15-minute sync.
    if (appIcons.length) {
      const { error: iconErr } = await supabase
        .from("app_icons")
        .upsert(appIcons, { onConflict: "slug", ignoreDuplicates: true });
      if (iconErr) console.error("app_icons upsert failed:", iconErr.message);
    }
    const { error } = await supabase
      .from("account_sync")
      .upsert(row, { onConflict: "license_key" });
    if (error) {
      console.error("account_sync upsert failed:", error.message);
      return NextResponse.json({ error: "Sync failed." }, { status: 500 });
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : "Store unavailable.";
    return NextResponse.json({ error: message }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
