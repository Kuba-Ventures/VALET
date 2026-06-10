"use client";

import { useEffect, useRef } from "react";

declare global {
  interface Window {
    dataLayer?: Record<string, unknown>[];
  }
}

/**
 * Pushes a single GTM dataLayer event on mount. Use for page-level conversion
 * signals (e.g. trial_started on the success page). Guarded so it fires once,
 * even under React strict-mode double-invocation in dev.
 */
export default function DataLayerEvent({
  event,
  plan,
  value,
}: {
  event: string;
  plan?: string | null;
  value?: number;
}) {
  const fired = useRef(false);
  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({
      event,
      ...(plan ? { plan } : {}),
      ...(value != null ? { value, currency: "USD" } : {}),
    });
  }, [event, plan, value]);
  return null;
}
