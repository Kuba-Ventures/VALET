import { getSupabaseAdmin } from "./supabase";

/**
 * Web-controlled device settings (bidirectional settings, Phase 1 data layer).
 * The web account writes these; the desktop app reads + applies them. Stored
 * per license. Everything here runs server-side with the service-role client;
 * the per-user helpers are ALWAYS scoped by a verified session user id.
 *
 * Only the allow-listed fields below are ever stored — extra keys are dropped.
 */

export interface DeviceSettings {
  voice?: "male" | "female";
  voice_id?: string;
  telemetry?: boolean;
}

export function sanitizeDeviceSettings(input: unknown): DeviceSettings {
  const src = (input && typeof input === "object" ? input : {}) as Record<string, unknown>;
  const out: DeviceSettings = {};
  if (src.voice === "male" || src.voice === "female") out.voice = src.voice;
  if (typeof src.voice_id === "string") {
    const v = src.voice_id.trim().slice(0, 120);
    if (v) out.voice_id = v;
  }
  if (typeof src.telemetry === "boolean") out.telemetry = src.telemetry;
  return out;
}

/** App-facing read, by license key (used behind the license-key proxy route). */
export async function getDeviceSettings(licenseKey: string): Promise<DeviceSettings> {
  const supabase = getSupabaseAdmin();
  const { data, error } = await supabase
    .from("device_settings")
    .select("settings")
    .eq("license_key", licenseKey)
    .maybeSingle();
  if (error) {
    console.error("getDeviceSettings failed:", error.message);
    return {};
  }
  return sanitizeDeviceSettings(data?.settings);
}

/** Web-facing read: the user's current settings (newest across their licenses). */
export async function getDeviceSettingsForUser(userId: string): Promise<DeviceSettings> {
  const supabase = getSupabaseAdmin();
  const { data: lic } = await supabase
    .from("licenses")
    .select("license_key")
    .eq("user_id", userId);
  const keys = (lic ?? []).map((r) => r.license_key as string);
  if (!keys.length) return {};

  const { data, error } = await supabase
    .from("device_settings")
    .select("settings, updated_at")
    .in("license_key", keys)
    .order("updated_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) {
    console.error("getDeviceSettingsForUser failed:", error.message);
    return {};
  }
  return sanitizeDeviceSettings(data?.settings);
}

/**
 * Outcome of a settings write. `no-license` means the account genuinely owns no
 * license; `error` means the write itself failed (and carries the DB message so
 * the real cause — e.g. a missing table/migration — is surfaced rather than
 * masked as "no license linked").
 */
export type SetDeviceSettingsResult =
  | { ok: true }
  | { ok: false; reason: "no-license" }
  | { ok: false; reason: "error"; detail: string };

/**
 * Web-facing write: apply settings to ALL of the user's licenses (account-wide,
 * so every device the user owns picks up the same preferences).
 */
export async function setDeviceSettingsForUser(
  userId: string,
  settings: DeviceSettings,
): Promise<SetDeviceSettingsResult> {
  const supabase = getSupabaseAdmin();
  const { data: lic, error: licErr } = await supabase
    .from("licenses")
    .select("license_key")
    .eq("user_id", userId);
  if (licErr) {
    console.error("setDeviceSettingsForUser license lookup failed:", licErr.message);
    return { ok: false, reason: "error", detail: licErr.message };
  }
  const keys = (lic ?? []).map((r) => r.license_key as string);
  if (!keys.length) return { ok: false, reason: "no-license" };

  const now = new Date().toISOString();
  const rows = keys.map((license_key) => ({
    license_key,
    settings,
    updated_at: now,
  }));
  const { error } = await supabase
    .from("device_settings")
    .upsert(rows, { onConflict: "license_key" });
  if (error) {
    console.error("setDeviceSettingsForUser write failed:", error.message);
    return { ok: false, reason: "error", detail: error.message };
  }
  return { ok: true };
}
