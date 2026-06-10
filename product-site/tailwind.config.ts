import type { Config } from "tailwindcss";

/**
 * All real values live in styles/tokens.css as CSS custom properties.
 * Tailwind only maps utility names onto those variables, so a future
 * style guide swap is a single file change (tokens.css), not a config rewrite.
 */
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "var(--color-bg)",
        "bg-elevated": "var(--color-bg-elevated)",
        panel: "var(--color-panel)",
        "panel-border": "var(--color-panel-border)",
        ink: "var(--color-ink)",
        "ink-dim": "var(--color-ink-dim)",
        "ink-faint": "var(--color-ink-faint)",
        accent: "var(--color-accent)",
        "accent-soft": "var(--color-accent-soft)",
        "accent-deep": "var(--color-accent-deep)",
      },
      fontFamily: {
        sans: "var(--font-sans)",
        mono: "var(--font-mono)",
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        DEFAULT: "var(--radius-md)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
      },
      spacing: {
        gutter: "var(--space-gutter)",
        section: "var(--space-section)",
      },
      boxShadow: {
        glow: "0 0 40px -8px var(--color-accent-soft)",
        "glow-lg": "0 0 80px -10px var(--color-accent-soft)",
        panel: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 20px 60px -30px rgba(0,0,0,0.8)",
      },
      maxWidth: {
        shell: "var(--width-shell)",
      },
    },
  },
  plugins: [],
};

export default config;
