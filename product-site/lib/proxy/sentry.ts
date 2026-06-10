/**
 * Opt-in proxy error reporting (Phase I).
 *
 * No-op unless SENTRY_DSN is set. Captures error *metadata* (surface, action,
 * exception) only — never request/response bodies, prompts, or audio text.
 * @sentry/node is loaded via a dynamic specifier so the proxy has no dependency
 * on it when telemetry is off (install it on the proxy to enable).
 */

let inited = false;

async function loadSentry(): Promise<any | null> {
  if (!process.env.SENTRY_DSN) return null;
  try {
    const mod = "@sentry/node";
    // Variable specifier keeps this an optional dependency (no build/type error
    // when the package isn't installed).
    const Sentry = await import(/* webpackIgnore: true */ mod);
    if (!inited) {
      Sentry.init({
        dsn: process.env.SENTRY_DSN,
        tracesSampleRate: 0,
        sendDefaultPii: false,
      });
      inited = true;
    }
    return Sentry;
  } catch {
    return null;
  }
}

export async function captureProxyError(action: string, error: unknown): Promise<void> {
  const Sentry = await loadSentry();
  if (!Sentry) return;
  try {
    Sentry.withScope((scope: any) => {
      scope.setTag("surface", "proxy");
      scope.setTag("action", action);
      Sentry.captureException(error instanceof Error ? error : new Error(String(error)));
    });
  } catch {
    /* never let telemetry break a request */
  }
}
