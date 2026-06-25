import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#171717",
        paper: "#fafafa",
        accent: "#006bff",
        accentSoft: "#e9f4ff",
        pine: "#107d32",
        blush: "#f2f2f2",
        muted: "#4d4d4d",
        border: "#0000001a",
        danger: "#ea001d",
        warning: "#ffae00",
        success: "#28a948"
      },
      fontFamily: {
        display: ["'Geist'", "'Geist Sans'", "Arial", "sans-serif"],
        body: ["'Geist'", "'Geist Sans'", "Arial", "sans-serif"],
        mono: ["'Geist Mono'", "'SFMono-Regular'", "Consolas", "monospace"]
      },
      boxShadow: {
        card: "0 2px 2px rgba(0, 0, 0, 0.04)"
      }
    }
  },
  plugins: []
};

export default config;
