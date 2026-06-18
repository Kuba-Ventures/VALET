import type { Metadata } from "next";
import WaitlistForm from "./WaitlistForm";

export const metadata: Metadata = {
  title: "Join the waitlist · VALET",
  description: "Request early access to VALET, your voice-first AI assistant.",
};

export default function WaitlistPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-shell flex-col items-center justify-center px-6 py-24">
      <WaitlistForm />
    </main>
  );
}
