import type { MetadataRoute } from "next";

// Must match the canonical www host (see sitemap.ts) so the Sitemap: line and
// the sitemap's own URLs don't point at the redirecting naked domain.
const BASE = process.env.NEXT_PUBLIC_SITE_URL || "https://www.valet-voice.com";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: ["/api/", "/success"] },
    sitemap: `${BASE}/sitemap.xml`,
  };
}
