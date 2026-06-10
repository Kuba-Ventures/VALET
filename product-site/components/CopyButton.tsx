"use client";

import { useState } from "react";

export default function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard can be blocked; fail quietly, the key is visible on screen.
    }
  }

  return (
    <button onClick={copy} className="btn-ghost shrink-0" aria-live="polite">
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
