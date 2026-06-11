import Link from "next/link";
import { redirect } from "next/navigation";
import { createSupabaseServerClient } from "@/lib/auth/server";
import {
  getAllAccounts,
  isAdmin,
  priceForPlan,
  type AdminAccount,
} from "@/lib/admin";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return "—";
  }
}

const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  trialing: "Trial",
  past_due: "Past due",
  canceled: "Canceled",
  invalid: "Inactive",
};

function StatusBadge({ status }: { status: string }) {
  const good = status === "active" || status === "trialing";
  const warn = status === "past_due";
  return (
    <span
      className={`label-mono rounded-full border px-2.5 py-0.5 ${
        good
          ? "border-accent/40 text-accent"
          : warn
            ? "border-[#ffb86b]/40 text-[#ffb86b]"
            : "border-panel-border text-ink-faint"
      }`}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel p-5">
      <div className="text-2xl font-bold tracking-tight">{value}</div>
      <div className="label-mono mt-1">{label}</div>
    </div>
  );
}

export default async function AdminPage() {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/account/login");
  if (!isAdmin(user.email)) redirect("/account");

  const accounts = await getAllAccounts();
  const active = accounts.filter((a) => a.status === "active");
  const trialing = accounts.filter((a) => a.status === "trialing");
  const mrr = active.reduce((sum, a) => sum + priceForPlan(a.planLabel), 0);
  const spend = accounts.reduce((sum, a) => sum + a.usage.estimated_cost_usd, 0);

  const renewOrTrial = (a: AdminAccount) =>
    a.status === "trialing" ? a.trialEndsAt : a.currentPeriodEnd;

  return (
    <main className="mx-auto min-h-screen max-w-shell px-6 py-24">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="label-mono text-accent/80">Admin</div>
          <h1 className="mt-1 text-3xl font-bold tracking-tight">Subscribers</h1>
          <p className="mt-1 text-sm text-ink-dim">
            {accounts.length} license{accounts.length === 1 ? "" : "s"} total
          </p>
        </div>
        <Link href="/account" className="btn-ghost !px-4 !py-2 text-sm">
          Back to account
        </Link>
      </div>

      <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Active" value={String(active.length)} />
        <StatCard label="On trial" value={String(trialing.length)} />
        <StatCard label="Est. MRR" value={`$${mrr.toLocaleString()}`} />
        <StatCard label="Period spend" value={`$${spend.toFixed(2)}`} />
      </div>

      <div className="panel mt-8 overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead>
            <tr className="border-b border-panel-border text-ink-faint">
              <th className="label-mono px-5 py-3 font-normal">Customer</th>
              <th className="label-mono px-5 py-3 font-normal">Tier</th>
              <th className="label-mono px-5 py-3 font-normal">Status</th>
              <th className="label-mono px-5 py-3 font-normal">Renews / Trial</th>
              <th className="label-mono px-5 py-3 font-normal">Joined</th>
              <th className="label-mono px-5 py-3 text-right font-normal">
                Requests
              </th>
              <th className="label-mono px-5 py-3 text-right font-normal">
                Est. cost
              </th>
              <th className="label-mono px-5 py-3 font-normal">Account</th>
            </tr>
          </thead>
          <tbody>
            {accounts.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-5 py-8 text-center text-ink-dim">
                  No licenses yet.
                </td>
              </tr>
            ) : (
              accounts.map((a, i) => (
                <tr
                  key={i}
                  className="border-b border-panel-border/50 last:border-0"
                >
                  <td className="px-5 py-3">
                    {a.customerEmail ?? (
                      <span className="text-ink-faint">unknown</span>
                    )}
                  </td>
                  <td className="px-5 py-3">{a.planLabel}</td>
                  <td className="px-5 py-3">
                    <StatusBadge status={a.status} />
                  </td>
                  <td className="px-5 py-3 text-ink-dim">
                    {formatDate(renewOrTrial(a))}
                  </td>
                  <td className="px-5 py-3 text-ink-dim">
                    {formatDate(a.createdAt)}
                  </td>
                  <td className="px-5 py-3 text-right">
                    {a.usage.requests.toLocaleString()}
                  </td>
                  <td className="px-5 py-3 text-right">
                    ${a.usage.estimated_cost_usd.toFixed(2)}
                  </td>
                  <td className="px-5 py-3">
                    {a.claimed ? (
                      <span className="text-accent">Linked</span>
                    ) : (
                      <span className="text-ink-faint">Unclaimed</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <p className="mt-4 text-xs text-ink-faint">
        Source of truth for billing is Stripe; this view reads the licenses store.
        Tier and MRR are derived from the plan price id.
      </p>
    </main>
  );
}
