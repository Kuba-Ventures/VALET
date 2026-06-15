import { NextRequest, NextResponse } from "next/server";
import { authorizeLicense } from "@/lib/proxy/auth";
import { recordUsage, enforceAllowance } from "@/lib/proxy/usage";
import { traceProxyCall } from "@/lib/proxy/langfuse";
import { estimateTtsCost } from "@/lib/proxy/pricing";
import { captureProxyError } from "@/lib/proxy/sentry";

/**
 * Thin passthrough proxy for Fish Audio TTS. The app sends { text, format?,
 * reference_id? }; we inject the server-held key, forward to Fish, and stream the
 * audio bytes back. Metered by character count (Fish returns no token usage).
 */

const FISH_API_URL = "https://api.fish.audio/v1/tts";
const DEFAULT_VOICE_ID = "612b878b113047d9a770c069c8b4fdfe";

export async function handleTtsProxy(req: NextRequest): Promise<Response> {
  const auth = await authorizeLicense(req);
  if (!auth.ok) return auth.response;

  // Fair-use ceiling (warn/throttle/block per FAIR_USE_MODE).
  const overLimit = await enforceAllowance(auth.licenseKey, "tts", auth.plan);
  if (overLimit) return overLimit;

  const apiKey = process.env.FISH_AUDIO_KEY ?? process.env.FISH_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "Proxy is missing FISH_AUDIO_KEY." },
      { status: 500 },
    );
  }

  let body: {
    text?: unknown; format?: unknown; reference_id?: unknown; speed?: unknown;
    latency?: unknown; model?: unknown;
  };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const text = typeof body.text === "string" ? body.text : "";
  if (!text.trim()) {
    return NextResponse.json({ error: "Missing text." }, { status: 400 });
  }
  const format = typeof body.format === "string" ? body.format : "mp3";
  const referenceId =
    typeof body.reference_id === "string"
      ? body.reference_id
      : process.env.FISH_VOICE_ID ?? DEFAULT_VOICE_ID;
  // Playback speed (Fish prosody). Clamped to a sane range; 1.0 = normal.
  const speed =
    typeof body.speed === "number" && body.speed >= 0.5 && body.speed <= 2.0
      ? body.speed
      : 1.0;
  // Latency tier (perceived-latency PR 3): Fish `latency` ∈ normal|balanced|low.
  // Forwarded so the app controls the latency/quality trade-off; defaults to
  // "normal" when absent/invalid, so older app builds behave exactly as before.
  const latency =
    body.latency === "balanced" || body.latency === "low" ? body.latency : "normal";
  // Optional TTS engine override → Fish `model` HTTP header. Left unset by
  // default so Fish picks its standard model for the cloned voice.
  const model = body.model === "s1" || body.model === "s2-pro" ? body.model : "";

  const startTime = Date.now();

  let upstream: Response;
  try {
    const fishHeaders: Record<string, string> = {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    };
    if (model) fishHeaders.model = model;
    upstream = await fetch(FISH_API_URL, {
      method: "POST",
      headers: fishHeaders,
      body: JSON.stringify({ text, reference_id: referenceId, format, prosody: { speed }, latency }),
    });
  } catch (err) {
    void captureProxyError("tts", err);
    const message = err instanceof Error ? err.message : "Upstream unreachable.";
    return NextResponse.json({ error: message }, { status: 502 });
  }

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    traceProxyCall({
      name: "tts",
      action: "tts",
      licenseKey: auth.licenseKey,
      model: "fish-audio",
      costUsd: 0,
      startTime,
      status: "error",
    });
    return NextResponse.json(
      { error: "TTS upstream error.", detail: detail.slice(0, 500) },
      { status: upstream.status },
    );
  }

  // Meter on character count once, up front (audio length is proportional to it).
  const costUsd = estimateTtsCost(text.length);
  void recordUsage({ licenseKey: auth.licenseKey, costUsd });
  traceProxyCall({
    name: "tts",
    action: "tts",
    licenseKey: auth.licenseKey,
    model: "fish-audio",
    costUsd,
    startTime,
    status: "ok",
  });

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? `audio/${format === "mp3" ? "mpeg" : format}`,
      "cache-control": "no-store",
    },
  });
}
