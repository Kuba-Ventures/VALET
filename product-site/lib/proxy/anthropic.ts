import { NextRequest, NextResponse, after } from "next/server";
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
 * Privacy-respecting action analytics. The assistant ends replies with a
 * structured tag like "[ACTION:OPEN_APP] Spotify". We extract just the action
 * TYPE (and, for opens, the app/project name, which is low-sensitivity) so the
 * dashboard can answer "what are people using it for" WITHOUT storing the raw
 * conversation. Targets for sensitive actions (deletes, builds, etc.) are not
 * captured, only their type.
 */
const ACTION_RE = /\[ACTION:([A-Z_]+)\]\s*([^\n\]]*)/g;
const KEEP_TARGET = new Set(["OPEN_APP", "OPEN_PROJECT"]);

function extractActions(text: string): string[] {
  const out: string[] = [];
  let m: RegExpExecArray | null;
  ACTION_RE.lastIndex = 0;
  while ((m = ACTION_RE.exec(text)) !== null) {
    const type = m[1].toLowerCase();
    out.push(type); // bare action type, always -> clean "actions done most" leaderboard
    if (KEEP_TARGET.has(m[1])) {
      const target = (m[2] || "").trim().slice(0, 48);
      if (target) out.push(`app:${target}`); // app/project name as its own tag
    }
  }
  return out;
}

/**
 * TransformStream that passes SSE bytes through untouched while scanning for the
 * usage carried on `message_start` / `message_delta`, and accumulating the reply
 * text so the final action tag(s) can be extracted. Calls `onDone` with the
 * tally and the actions at stream end.
 */
function meteringTransform(
  onDone: (usage: TokenUsage, actions: string[]) => unknown,
): TransformStream<Uint8Array, Uint8Array> {
  const decoder = new TextDecoder();
  const usage = emptyUsage();
  let buf = "";
  let text = "";

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
          } else if (evt.type === "content_block_delta" && evt.delta?.type === "text_delta") {
            text += evt.delta.text ?? "";
          }
        } catch {
          // partial or non-JSON data line — ignore
        }
      }
    },
    async flush() {
      // Await metering/tracing here: the stream (and thus the serverless
      // function) stays alive until the trace is delivered. Without this the
      // Langfuse POST is dropped when the function suspends.
      await onDone(usage, extractActions(text));
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
  const overLimit = await enforceAllowance(auth.licenseKey, actionType, auth.plan);
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

  // Returns a promise that settles once usage is recorded AND the Langfuse
  // trace is delivered. Callers MUST keep the function alive until it settles
  // (via `after()` on the non-streaming path, or by awaiting it in the stream
  // flush) — on Vercel an un-awaited side effect is dropped when the function
  // suspends after the response, which silently lost all usage + traces.
  const meter = (
    usage: TokenUsage,
    status: "ok" | "error",
    actions: string[] = [],
  ): Promise<unknown> => {
    const costUsd = estimateModelCost(model, usage);
    return Promise.allSettled([
      recordUsage({
        licenseKey: auth.licenseKey,
        inputTokens:
          usage.input_tokens +
          usage.cache_creation_input_tokens +
          usage.cache_read_input_tokens,
        outputTokens: usage.output_tokens,
        costUsd,
      }),
      traceProxyCall({
        name: actionType,
        action: actionType,
        licenseKey: auth.licenseKey,
        model,
        usage,
        costUsd,
        startTime,
        status,
        actionsRequested: actions,
      }),
    ]);
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
      const replyText = Array.isArray(json.content)
        ? json.content.filter((b: { type?: string }) => b.type === "text").map((b: { text?: string }) => b.text ?? "").join("")
        : "";
      // `after()` keeps the function alive until metering + tracing finish,
      // without delaying this response.
      after(() =>
        meter(
          {
            input_tokens: json.usage.input_tokens ?? 0,
            output_tokens: json.usage.output_tokens ?? 0,
            cache_creation_input_tokens: json.usage.cache_creation_input_tokens ?? 0,
            cache_read_input_tokens: json.usage.cache_read_input_tokens ?? 0,
          },
          "ok",
          extractActions(replyText),
        ),
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
    meteringTransform((usage, actions) => meter(usage, "ok", actions)),
  );
  return new Response(stream, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "text/event-stream",
      "cache-control": "no-store",
    },
  });
}
