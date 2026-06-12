"use client";

import { useEffect, useState } from "react";

/**
 * Editable device settings (bidirectional settings, Phase 2). Reads/writes the
 * web-controlled settings the desktop app applies — voice, Voice ID (advanced),
 * and telemetry. Saves to /api/account/device-settings; the app picks them up
 * on its next fetch (Phase 3).
 */
interface DeviceSettings {
  voice?: "male" | "female";
  voice_id?: string;
  telemetry?: boolean;
}

export default function DeviceSettingsCard() {
  const [settings, setSettings] = useState<DeviceSettings>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/account/device-settings");
        const data = await res.json();
        if (res.ok && data.settings) setSettings(data.settings as DeviceSettings);
      } catch {
        /* leave defaults */
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  function update(patch: Partial<DeviceSettings>) {
    setSettings((s) => ({ ...s, ...patch }));
    setSaved(false);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch("/api/account/device-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not save.");
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setSaving(false);
    }
  }

  const voice = settings.voice ?? "male";

  return (
    <div className="panel p-6 sm:p-8">
      <div className="label-mono mb-1">Device settings</div>
      <p className="mb-5 text-sm text-ink-dim">
        Set these here and your VALET app applies them automatically.
      </p>

      {loading ? (
        <div className="text-sm text-ink-faint">Loading…</div>
      ) : (
        <div className="flex flex-col gap-5">
          {/* Voice */}
          <div>
            <div className="label-mono mb-2">Voice</div>
            <div className="flex gap-2">
              {(["male", "female"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => update({ voice: v })}
                  className={`settings-voice-opt rounded-md border px-4 py-2 text-sm transition-colors ${
                    voice === v
                      ? "border-accent/50 bg-[rgba(56,225,255,0.08)] text-accent"
                      : "border-panel-border text-ink-dim hover:border-accent-soft"
                  }`}
                >
                  British {v === "male" ? "Male" : "Female"}
                </button>
              ))}
            </div>
          </div>

          {/* Voice ID (advanced) — moved here from the app's Console Settings */}
          <label className="flex flex-col gap-1.5">
            <span className="label-mono">Voice ID (advanced)</span>
            <input
              type="text"
              value={settings.voice_id ?? ""}
              onChange={(e) => update({ voice_id: e.target.value })}
              className="input-field w-full font-mono"
              placeholder="Fish reference_id override (optional)"
            />
          </label>

          {/* Telemetry */}
          <label className="flex items-center gap-2.5 text-sm">
            <input
              type="checkbox"
              checked={settings.telemetry ?? true}
              onChange={(e) => update({ telemetry: e.target.checked })}
            />
            <span>Share crash + error reports</span>
          </label>

          <div className="flex items-center gap-3">
            <button onClick={save} disabled={saving} className="btn-primary !px-5 !py-2 text-sm">
              {saving ? "Saving…" : "Save"}
            </button>
            {saved && <span className="text-sm text-accent">Saved ✓</span>}
            {error && (
              <span className="text-sm text-[#ff8a8a]" role="alert">
                {error}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
