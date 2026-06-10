/**
 * Langfuse tracing over the public HTTP ingestion API (no SDK dependency).
 *
 * Every proxied call produces one trace with a nested generation, with the
 * license as the Langfuse user id. Payloads (prompts, message bodies, audio
 * text) are SCRUBBED by default: we log metadata only (model, tokens, cost,
 * latency, action, status). Set PROXY_CAPTURE_PAYLOADS=true in development to
 * also capture input/output. Fire-and-forget — never blocks or breaks a call.
 */

interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
}

export interface TraceArgs {
  name: string; // e.g. "completion" | "research" | "tts"
  action: string;
  licenseKey: string;
  model: string;
  usage?: TokenUsage;
  costUsd: number;
  startTime: number; // epoch ms
  status: "ok" | "error";
  input?: unknown; // captured only when PROXY_CAPTURE_PAYLOADS=true
  output?: unknown; // captured only when PROXY_CAPTURE_PAYLOADS=true
}

export function traceProxyCall(args: TraceArgs): void {
  const host =
    process.env.LANGFUSE_HOST ||
    process.env.LANGFUSE_BASE_URL ||
    "https://cloud.langfuse.com";
  const pk = process.env.LANGFUSE_PUBLIC_KEY;
  const sk = process.env.LANGFUSE_SECRET_KEY;
  if (!pk || !sk) return; // Langfuse not configured — no-op.

  const capture = process.env.PROXY_CAPTURE_PAYLOADS === "true";
  const startIso = new Date(args.startTime).toISOString();
  const endIso = new Date().toISOString();
  const traceId = crypto.randomUUID();
  const genId = crypto.randomUUID();

  const metadata = {
    action: args.action,
    estimated_cost_usd: Number(args.costUsd.toFixed(6)),
    latency_ms: Date.now() - args.startTime,
    status: args.status,
  };

  const body = {
    batch: [
      {
        id: crypto.randomUUID(),
        type: "trace-create",
        timestamp: endIso,
        body: {
          id: traceId,
          name: args.name,
          userId: args.licenseKey,
          metadata,
          ...(capture && args.input !== undefined ? { input: args.input } : {}),
        },
      },
      {
        id: crypto.randomUUID(),
        type: "generation-create",
        timestamp: endIso,
        body: {
          id: genId,
          traceId,
          name: args.name,
          model: args.model,
          startTime: startIso,
          endTime: endIso,
          metadata,
          ...(args.usage
            ? {
                usage: {
                  input: args.usage.input_tokens,
                  output: args.usage.output_tokens,
                  total: args.usage.input_tokens + args.usage.output_tokens,
                  unit: "TOKENS",
                },
              }
            : {}),
          ...(capture && args.output !== undefined ? { output: args.output } : {}),
        },
      },
    ],
  };

  const auth = "Basic " + Buffer.from(`${pk}:${sk}`).toString("base64");
  // Intentionally not awaited; swallow all errors.
  fetch(`${host}/api/public/ingestion`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: auth },
    body: JSON.stringify(body),
  }).catch(() => {});
}
