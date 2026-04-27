import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#112031",
        paper: "#f7f3e9",
        accent: "#b5532d",
        accentSoft: "#e1b477",
        pine: "#1f5c4b",
        blush: "#f1d7c9"
      },
      fontFamily: {
        display: ["'Fraunces'", "serif"],
        body: ["'IBM Plex Sans'", "sans-serif"]
      },
      boxShadow: {
        card: "0 24px 60px rgba(17, 32, 49, 0.12)"
      }
    }
  },
  plugins: []
};

export default config;
