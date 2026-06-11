import UpdatePasswordForm from "@/components/account/UpdatePasswordForm";

export const dynamic = "force-dynamic";

export default function UpdatePasswordPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-shell flex-col items-center justify-center px-6 py-24">
      <UpdatePasswordForm />
    </main>
  );
}
