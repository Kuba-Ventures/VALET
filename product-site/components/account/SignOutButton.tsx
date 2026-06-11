"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/auth/client";

export default function SignOutButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function signOut() {
    setLoading(true);
    const supabase = createSupabaseBrowserClient();
    await supabase.auth.signOut();
    router.replace("/account/login");
    router.refresh();
  }

  return (
    <button onClick={signOut} disabled={loading} className="btn-ghost !px-4 !py-2 text-sm">
      {loading ? "Signing out..." : "Sign out"}
    </button>
  );
}
