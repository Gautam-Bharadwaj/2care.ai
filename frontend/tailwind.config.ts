import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

/**
 * Tailwind theme. Color tokens are wired to CSS custom properties defined
 * in `src/index.css`, so `bg-primary` / `text-foreground` / etc. respect
 * the live dark-mode toggle and any future theme swap without a rebuild.
 *
 * Brand:
 *   - primary  oklch(0.55 0.095 195)   deep teal
 *   - accent   oklch(0.62 0.165 35)    warm coral, reserved for user-speaking
 *   - success  oklch(0.50 0.120 155)
 *
 * Matches `docs/voice/voice.css` so the new shadcn build looks the same as
 * the previously-approved design.
 */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "1.5rem",
      screens: { "2xl": "1280px" },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
          ring: "hsl(var(--primary-ring))",
          soft: "hsl(var(--primary-soft))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
          ring: "hsl(var(--accent-ring))",
          soft: "hsl(var(--accent-soft))",
        },
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
          soft: "hsl(var(--success-soft))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "Menlo", "monospace"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 4px)",
        sm: "calc(var(--radius) - 8px)",
      },
      keyframes: {
        // Idle orb breath
        "orb-idle": {
          "0%, 100%": { transform: "scale(1)", opacity: "1" },
          "50%": { transform: "scale(1.03)", opacity: "0.95" },
        },
        // Ring expanding outward — used in agent_speaking + user_speaking
        "ring-out": {
          "0%": { transform: "scale(1)", opacity: "0.45", borderWidth: "1.5px" },
          "100%": { transform: "scale(1.85)", opacity: "0", borderWidth: "0.5px" },
        },
        // Three rotating dots for "thinking"
        "dot-orbit": {
          from: { transform: "rotate(0deg)" },
          to: { transform: "rotate(360deg)" },
        },
        // Health/connected indicator
        "healthbeat": {
          "0%, 100%": { boxShadow: "0 0 0 0 hsl(var(--success) / 0.5)" },
          "50%": { boxShadow: "0 0 0 5px hsl(var(--success) / 0)" },
        },
        // Transcript fade-up
        "line-fade": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "orb-idle": "orb-idle 2s ease-in-out infinite",
        "ring-out-1": "ring-out 2.4s ease-out infinite",
        "ring-out-2": "ring-out 2.4s ease-out 0.8s infinite",
        "ring-out-3": "ring-out 2.4s ease-out 1.6s infinite",
        "dot-orbit": "dot-orbit 1.4s linear infinite",
        "healthbeat": "healthbeat 1.5s ease-in-out infinite",
        "line-fade": "line-fade 0.28s ease-out",
      },
    },
  },
  plugins: [animate],
} satisfies Config;
