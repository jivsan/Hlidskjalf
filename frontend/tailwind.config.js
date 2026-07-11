/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#1a1b26",
        surface: "#24283b",
        "border-token": "#2f3549",
        fg: "#c0caf5",
        muted: "#565f89",
        pink: "#ff4fa3",
        cyan: "#2de2e6",
        red: "#f7768e",
        amber: "#e0af68",
      },
      fontFamily: {
        mono: ['"JetBrainsMono Nerd Font"', '"JetBrains Mono"', "monospace"],
      },
      borderRadius: {
        card: "8px",
      },
    },
  },
  plugins: [],
};
