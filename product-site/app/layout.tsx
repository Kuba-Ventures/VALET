import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";

// Google Tag Manager container. The GTM ID is public (it appears in the page
// source of every site that uses GTM), so it is safe to commit. Override or
// point at a different container via NEXT_PUBLIC_GTM_ID. Everything downstream
// (GA4, conversions, pixels) is configured in the GTM UI, no code changes.
const GTM_ID = process.env.NEXT_PUBLIC_GTM_ID || "GTM-PV2FK8J3";

// Canonical host — the www apex, which the naked domain 308-redirects to. Setting
// metadataBase makes Next emit absolute canonical/OG URLs on this host, so Google
// stops treating the redirecting non-www URLs as the pages to index.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://www.valet-voice.com";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: "VALET: talk to your Mac, it does the work",
  description:
    "Say what you want in plain words. VALET reaches into the apps you already use and gets it done. Voice-first control for macOS.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
        {GTM_ID && (
          <Script id="gtm-loader" strategy="afterInteractive">
            {`(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);})(window,document,'script','dataLayer','${GTM_ID}');`}
          </Script>
        )}
      </head>
      <body>
        {GTM_ID && (
          <noscript>
            <iframe
              src={`https://www.googletagmanager.com/ns.html?id=${GTM_ID}`}
              height="0"
              width="0"
              style={{ display: "none", visibility: "hidden" }}
            />
          </noscript>
        )}
        <Nav />
        {children}
        <Footer />
      </body>
    </html>
  );
}
