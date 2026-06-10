import Orb from "./Orb";
import CheckoutButton from "./CheckoutButton";

export default function Hero() {
  return (
    <section className="relative mx-auto flex min-h-[88vh] max-w-shell flex-col items-center px-6 pt-20 pb-16 md:pt-28">
      <div className="label-mono mb-8">Voice-first computer control</div>

      <div className="grid w-full items-center gap-10 md:grid-cols-2">
        <div className="order-2 text-center md:order-1 md:text-left">
          <h1 className="text-[2.75rem] font-bold leading-[1.05] tracking-tight md:text-[3.75rem]">
            Talk to your computer.
            <br />
            <span className="text-accent">It does the work.</span>
          </h1>

          <p className="mx-auto mt-6 max-w-md text-lg leading-relaxed text-ink-dim md:mx-0">
            VALET is a voice assistant that acts across your apps. Ask
            in plain words. It stays fast for the simple things and goes deep on
            the hard ones. Everything is included, no API key of your own
            required.
          </p>

          <div className="mt-9 flex flex-col items-center gap-4 sm:flex-row md:items-start md:justify-start">
            <CheckoutButton />
            <a href="#how" className="btn-ghost">
              See how it works
            </a>
          </div>

          <p className="mt-4 text-sm text-ink-faint">
            $20 per month. 7 day free trial. Cancel anytime.
          </p>
        </div>

        <div className="order-1 md:order-2">
          <Orb />
        </div>
      </div>
    </section>
  );
}
