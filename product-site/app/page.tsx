import HomeHero from "@/components/home/HomeHero";
import HomeShowcase from "@/components/home/HomeShowcase";
import HomeConnections from "@/components/home/HomeConnections";
import HomeClosing from "@/components/home/HomeClosing";

/**
 * Home page (redesign). The `.home` class scopes the violet brand skin to this
 * page only — Nav/Footer render outside <main> and keep the site's cyan system.
 *
 * Download CTAs point at the configured installer when DOWNLOAD_URL is set,
 * otherwise into the pricing funnel (where a license — and thus the gated
 * download — is obtained). The real .dmg URL is never hardcoded here.
 */
export default function Home() {
  const downloadHref = process.env.DOWNLOAD_URL || "/pricing";

  return (
    <main className="home">
      <HomeHero downloadHref={downloadHref} />
      <HomeShowcase />
      <HomeConnections />
      <HomeClosing downloadHref={downloadHref} />
    </main>
  );
}
