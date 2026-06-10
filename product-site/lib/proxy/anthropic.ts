import { NextRequest, NextResponse } from "next/server";
import { authorizeLicense } from "@/lib/proxy/auth";
import { recordUsage, enforceAllowance } from "@/lib/proxy/usage";
import { traceProxyCall } from "@/lib/proxy/langfuse";
import { estimateModelCost, type TokenUsage } from "@/lib/proxy/pricing";
import { captureProxyError } from "@/lib/proxy/sentry";

/**
 * Thin passthrough proxy for the Anthropic Messages API. The desktop app speaks
 * the native Messages dialect; we inject the server-held key, forward to
 * Anthropic, stream the response back byte-for-byte, and meter usage out of band.
 *
 * Two endpoints share this handler — `completion` (Haiku conversation) and
 * `research` (Opus + web tools) — differing only in their action tag and route
 * maxDuration. The model is read from the request body, so this stays generic.
 */

const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";
const ANTHROPIC_VERSION = "2023-06-01";

function emptyUsage(): TokenUsage {
  return {
    input_tokens: 0,
    output_tokens: 0,
    cache_creation_input_tokens: 0,
    cache_read_input_tokens: 0,
  };
}

/**
 * TransformStream that passes SSE bytes through untouched while scanning for the
 * usage carried on `message_start` (input + cache tokens) and `message_delta`
 * (cumulative output tokens). Calls `onDone` with the final tally at stream end.
 */
function meteringTransform(
  onDone: (usage: TokenUsage) => void,
): TransformStream<Uint8Array, Uint8Array> {
  const decoder = new TextDecoder();
  const usage = emptyUsage();
  let buf = "";

  return new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      controller.enqueue(chunk); // forward immediately, no buffering of bytes
      buf += decoder.decode(chunk, { stream: true });
      let nl: number;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (!data || data === "[DONE]") continue;
        try {
          const evt = JSON.parse(data);
          if (evt.type === "message_start" && evt.message?.usage) {
            const u = evt.message.usage;
            usage.input_tokens = u.input_tokens ?? 0;
            usage.cache_creation_input_tokens = u.cache_creation_input_tokens ?? 0;
            usage.cache_read_input_tokens = u.cache_read_input_tokens ?? 0;
            usage.output_tokens = u.output_tokens ?? 0;
          } else if (evt.type === "message_delta" && evt.usage) {
            if (typeof evt.usage.output_tokens === "number") {
              usage.output_tokens = evt.usage.output_tokens;
            }
            if (typeof evt.usage.input_tokens === "number") {
              usage.input_tokens = evt.usage.input_tokens;
            }
          }
        } catch {
          // partial or non-JSON data line — ignore
        }
      }
    },
    flush() {
      onDone(usage);
    },
  });
}

export async function handleAnthropicProxy(
  req: NextRequest,
  actionType: string,
): Promise<Response> {
  const auth = await authorizeLicense(req);
  if (!auth.ok) return auth.response;

  // Fair-use ceiling (warn/throttle/block per FAIR_USE_MODE).
  const overLimit = await enforceAllowance(auth.licenseKey, actionType);
  if (overLimit) return overLimit;

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "Proxy is missing ANTHROPIC_API_KEY." },
      { status: 500 },
    );
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const model = typeof body.model === "string" ? body.model : "unknown";
  const isStream = body.stream === true;
  const startTime = Date.now();

  const meter = (usage: TokenUsage, status: "ok" | "error") => {
    const costUsd = estimateModelCost(model, usage);
    void recordUsage({
      licenseKey: auth.licenseKey,
      inputTokens:
        usage.input_tokens +
        usage.cache_creation_input_tokens +
        usage.cache_read_input_tokens,
      outputTokens: usage.output_tokens,
      costUsd,
    });
    traceProxyCall({
      name: actionType,
      action: actionType,
      licenseKey: auth.licenseKey,
      model,
      usage,
      costUsd,
      startTime,
      status,
    });
  };

  let upstream: Response;
  try {
    upstream = await fetch(ANTHROPIC_URL, {
      method: "POST",
      headers: {
        "x-api-key": apiKey,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
    });
  } catch (err) {
    void captureProxyError(actionType, err);
    const message = err instanceof Error ? err.message : "Upstream unreachable.";
    return NextResponse.json({ error: message }, { status: 502 });
  }

  // Non-streaming: buffer, read usage from the response, return JSON.
  if (!isStream) {
    const json = await upstream.json().catch(() => null);
    if (json && upstream.ok && json.usage) {
      meter(
        {
          input_tokens: json.usage.input_tokens ?? 0,
          output_tokens: json.usage.output_tokens ?? 0,
          cache_creation_input_tokens: json.usage.cache_creation_input_tokens ?? 0,
          cache_read_input_tokens: json.usage.cache_read_input_tokens ?? 0,
        },
        "ok",
      );
    }
    return NextResponse.json(json ?? { error: "Empty upstream response." }, {
      status: upstream.status,
    });
  }

  // Streaming error before the stream starts: pass the error body straight back.
  if (!upstream.ok || !upstream.body) {
    const text = await upstream.text().catch(() => "");
    return new NextResponse(text || JSON.stringify({ error: "Upstream error." }), {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  }

  // Streaming: tee through the metering transform; meter fires on stream end.
  const stream = upstream.body.pipeThrough(
    meteringTransform((usage) => meter(usage, "ok")),
  );
  return new Response(stream, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "text/event-stream",
      "cache-control": "no-store",
    },
  });
}
