import AuthForm from "@/components/account/AuthForm";

export const dynamic = "force-dynamic";

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;
  return (
    <main className="mx-auto flex min-h-screen max-w-shell flex-col items-center justify-center px-6 py-24">
      <AuthForm mode="signup" next={next || "/account"} />
    </main>
  );
}
