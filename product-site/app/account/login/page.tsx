import AuthForm from "@/components/account/AuthForm";

export const dynamic = "force-dynamic";

const MESSAGES: Record<string, string> = {
  link: "That link has expired or was already used. Sign in instead.",
  auth: "Please sign in to continue.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next, error } = await searchParams;
  const note = error ? MESSAGES[error] : null;

  return (
    <main className="mx-auto flex min-h-screen max-w-shell flex-col items-center justify-center px-6 py-24">
      {note && (
        <p className="mb-5 max-w-md text-center text-sm text-ink-dim">{note}</p>
      )}
      <AuthForm mode="login" next={next || "/account"} />
    </main>
  );
}
