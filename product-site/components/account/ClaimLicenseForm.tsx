"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/**
 * Manual license claim. Most licenses auto-attach by matching the buyer email,
 * but if someone bought with a different address they can paste the key here.
 */
export default function ClaimLicenseForm() {
  const router = useRouter();
  const [key, setKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/account/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ license_key: key.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not link that key.");
      setKey("");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row sm:items-start">
      <div className="flex-1">
        <input
          type="text"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          className="input-field w-full font-mono"
          placeholder="PRODUCT-XXXX-XXXX-XXXX-XXXX"
          aria-label="License key"
        />
        {error && (
          <span className="mt-2 block text-sm text-[#ff8a8a]" role="alert">
            {error}
          </span>
        )}
      </div>
      <button type="submit" disabled={loading || !key.trim()} className="btn-primary">
        {loading ? "Linking..." : "Link key"}
      </button>
    </form>
  );
}
