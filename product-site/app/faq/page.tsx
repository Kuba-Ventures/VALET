import FaqAccordion from "@/components/FaqAccordion";

export const metadata = {
  title: "VALET: FAQ",
  description: "Straight answers about what VALET runs on, the free trial, cancellation, and privacy.",
  alternates: { canonical: "/faq" },
};

export default function FaqPage() {
  return (
    <main className="pt-32 pb-24">
      <div className="shell">
        <p className="eyebrow">FAQ</p>
        <h1 className="mt-4 h-display text-4xl md:text-6xl text-ink">
          Straight answers.
        </h1>

        <div className="mt-14">
          <FaqAccordion />
        </div>
      </div>
    </main>
  );
}
