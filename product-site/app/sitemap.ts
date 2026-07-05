import type { MetadataRoute } from "next";

// Canonical host is the www apex — the naked domain 308-redirects to it, so the
// sitemap MUST list www URLs or Google flags every entry as "Page with redirect"
// and indexes the destination instead. Keep NEXT_PUBLIC_SITE_URL (Vercel prod)
// on the www host so this, robots, and Stripe redirects all agree.
const BASE = process.env.NEXT_PUBLIC_SITE_URL || "https://www.valet-voice.com";

// Public, indexable routes. Excludes /success and /api (post-purchase / internal).
const ROUTES = ["", "/how-it-works", "/pricing", "/faq", "/contact", "/privacy", "/terms"];

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();
  return ROUTES.map((path) => ({
    url: `${BASE}${path}`,
    lastModified,
    changeFrequency: "monthly",
    priority: path === "" ? 1 : 0.7,
  }));
}
