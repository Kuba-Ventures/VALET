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
  for (const key of ["top_apps", "top_sites"] as const) {
    if (Array.isArray(src[key])) {
      out[key] = (src[key] as unknown[])
        .map((a) => {
          const label = str((a as Record<string, unknown>)?.label, 80);
          const count = num((a as Record<string, unknown>)?.count);
          return label ? { label, count: count ? Math.round(count) : 0 } : null;
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
  if (profile) row.profile = profile;
  if (stats) row.stats = stats;
  if (connections) row.connections = connections;
  if (appVersion) row.app_version = appVersion;

  try {
    const supabase = getSupabaseAdmin();
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
