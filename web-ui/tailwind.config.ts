import type { Config } from "tailwindcss";

/*
 * Atlassian Design System color ramps (https://atlassian.design/foundations/color).
 * We remap Tailwind's default palettes onto Atlassian's accent ramps so the
 * whole app picks up the "Jira" look without touching individual components:
 *   neutral→slate/gray, blue→indigo/blue, teal→sky/cyan, purple→violet,
 *   green→emerald, yellow→amber, red→rose/red.
 * Semantic tokens (primary, surfaces, border…) live as CSS vars in index.css.
 */
const N = {
  50: "#F4F5F7",
  100: "#EBECF0",
  200: "#DFE1E6",
  300: "#C1C7D0",
  400: "#A5ADBA",
  500: "#7A869A",
  600: "#5E6C84",
  700: "#42526E",
  800: "#253858",
  900: "#172B4D",
};
const B = {
  50: "#E9F2FF",
  100: "#CCE0FF",
  200: "#85B8FF",
  300: "#579DFF",
  400: "#388BFF",
  500: "#1D7AFC",
  600: "#0C66E4",
  700: "#0055CC",
  800: "#09326C",
  900: "#082145",
};
const G = {
  50: "#E3FCEF",
  100: "#ABF5D1",
  200: "#79F2C0",
  300: "#57D9A3",
  400: "#36B37E",
  500: "#00875A",
  600: "#006644",
  700: "#005C3D",
  800: "#003824",
  900: "#002918",
};
const Y = {
  50: "#FFFAE6",
  100: "#FFF0B3",
  200: "#FFE380",
  300: "#FFC400",
  400: "#FFAB00",
  500: "#FF991F",
  600: "#FF8B00",
  700: "#B86E00",
  800: "#7F5400",
  900: "#533F04",
};
const R = {
  50: "#FFEBE6",
  100: "#FFBDAD",
  200: "#FF8F73",
  300: "#FF7452",
  400: "#FF5630",
  500: "#DE350B",
  600: "#BF2600",
  700: "#971C00",
  800: "#5E1404",
  900: "#42190C",
};
const P = {
  50: "#EAE6FF",
  100: "#C0B6F2",
  200: "#998DD9",
  300: "#8777D9",
  400: "#6554C0",
  500: "#5243AA",
  600: "#403294",
  700: "#332B7A",
  800: "#231C5C",
  900: "#15103B",
};
const T = {
  50: "#E6FCFF",
  100: "#B3F5FF",
  200: "#79E2F2",
  300: "#00C7E6",
  400: "#00B8D9",
  500: "#00A3BF",
  600: "#008DA6",
  700: "#0B7077",
  800: "#1D474C",
  900: "#10363A",
};

const config: Config = {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        // Atlassian Sans first (loaded from Atlassian's CDN in index.html —
        // the exact font Jira ships today), then matching fallbacks.
        sans: [
          '"Atlassian Sans"',
          '"Inter Variable"',
          "Inter",
          '"Hanken Grotesk Variable"',
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
        serif: [
          '"Tiempos Text"',
          '"Galaxie Copernicus"',
          '"Source Serif 4 Variable"',
          "Georgia",
          "serif",
        ],
        mono: [
          '"Atlassian Mono"',
          '"Geist Mono"',
          '"JetBrains Mono Variable"',
          "ui-monospace",
          "monospace",
        ],
      },
      colors: {
        // Brand accent → Atlassian Blue (primary actions, links, active, focus).
        brand: B,
        // Remap Tailwind's raw palettes onto Atlassian accent ramps so existing
        // `slate-*`, `indigo-*`, `emerald-*`… utilities render the Jira look.
        slate: N,
        gray: N,
        zinc: N,
        neutral: N,
        stone: N,
        indigo: B,
        blue: B,
        sky: T,
        cyan: T,
        teal: T,
        violet: P,
        purple: P,
        fuchsia: P,
        emerald: G,
        green: G,
        lime: G,
        amber: Y,
        yellow: Y,
        orange: Y,
        rose: R,
        red: R,
        pink: R,
        // Semantic status roles (Atlassian) for badges / status chips.
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        warning: {
          DEFAULT: "hsl(var(--warning))",
          foreground: "hsl(var(--warning-foreground))",
        },
        discovery: {
          DEFAULT: "hsl(var(--discovery))",
          foreground: "hsl(var(--discovery-foreground))",
        },
        border: "hsl(var(--border))",
        "border-strong": "hsl(var(--border-strong))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        "foreground-strong": "hsl(var(--foreground-strong))",
        surface: {
          1: "hsl(var(--surface-1))",
          2: "hsl(var(--surface-2))",
          3: "hsl(var(--surface-3))",
        },
        nav: {
          DEFAULT: "hsl(var(--nav))",
          foreground: "hsl(var(--nav-foreground))",
          hover: "hsl(var(--nav-hover))",
          selected: "hsl(var(--nav-selected))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },
      borderRadius: {
        // Classic-Jira square shape scale (the demo uses 2–4px everywhere).
        sm: "2px", // chips, tags
        DEFAULT: "3px", // buttons, inputs, cards
        md: "3px",
        lg: "var(--radius)", // 4px — dialogs, popovers
        xl: "0.5rem",
        "2xl": "0.5rem", // cap big radii to keep the Jira-square feel
      },
      boxShadow: {
        // ADS elevation (theme-aware via CSS vars in index.css).
        raised: "var(--shadow-raised)",
        overlay: "var(--shadow-overlay)",
        // Jira issue card: borderless white card floating on a gray lane.
        card: "var(--shadow-card)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.18s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
