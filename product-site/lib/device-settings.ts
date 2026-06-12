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
 * Web-facing write: apply settings to ALL of the user's licenses (account-wide,
 * so every device the user owns picks up the same preferences). Returns false if
 * the user has no licenses.
 */
export async function setDeviceSettingsForUser(
  userId: string,
  settings: DeviceSettings,
): Promise<boolean> {
  const supabase = getSupabaseAdmin();
  const { data: lic } = await supabase
    .from("licenses")
    .select("license_key")
    .eq("user_id", userId);
  const keys = (lic ?? []).map((r) => r.license_key as string);
  if (!keys.length) return false;

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
    console.error("setDeviceSettingsForUser failed:", error.message);
    return false;
  }
  return true;
}
