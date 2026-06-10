import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "[JARVIS] — talk to your computer",
  description:
    "A voice-first assistant you talk to. It acts across your apps, stays fast for simple things and goes deep for hard ones. Everything included, no API key needed.",
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
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
