/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Deep night. bg is the void the seat looks out over; two surface levels
        // give cards real elevation instead of one flat gray.
        bg: "#15161f",
        abyss: "#0e0e16", // deepest — data wells, faceplate backing
        surface: "#1e2030",
        "surface-2": "#262a41", // raised surfaces (active rows, menus)
        "border-token": "#2b2f45",
        fg: "#c8d3f5",
        muted: "#727aa3", // brightened for legibility on the deeper bg
        // Accents — kept exactly (established brand + hardcoded chart/faceplate
        // parity). Elevation comes from discipline, not new hues:
        //   cyan = live / healthy / primary signal · pink = seat / selection
        //   amber = attention · red = danger
        pink: "#ff4fa3",
        cyan: "#2de2e6",
        red: "#f7768e",
        amber: "#e0af68",
      },
      fontFamily: {
        // Archivo carries the human interface; JetBrains Mono the machine's numbers.
        sans: ['"Archivo Variable"', "system-ui", "sans-serif"],
        display: ['"Archivo Variable"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', '"JetBrainsMono Nerd Font"', "monospace"],
      },
      borderRadius: {
        card: "10px",
      },
      letterSpacing: {
        eyebrow: "0.18em",
      },
    },
  },
  plugins: [],
};
