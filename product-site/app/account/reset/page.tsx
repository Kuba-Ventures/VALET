import ResetForm from "@/components/account/ResetForm";

export const dynamic = "force-dynamic";

export default function ResetPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-shell flex-col items-center justify-center px-6 py-24">
      <ResetForm />
    </main>
  );
}
