import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Recharts drags in d3 (via victory-vendor) and lodash; pinning it to its own
// vendor chunk keeps that mass in one cacheable file shared by the three chart
// consumers (NodePage, VM graphs tab, VM overview sparklines) instead of being
// smeared across their route chunks. It is only ever reached through a dynamic
// import, so it never loads on first paint.
const CHART_VENDOR = [
  "recharts",
  "recharts-scale",
  "react-smooth",
  "victory-vendor",
  "d3-array",
  "d3-color",
  "d3-format",
  "d3-interpolate",
  "d3-path",
  "d3-scale",
  "d3-shape",
  "d3-time",
  "d3-time-format",
  "internmap",
  "lodash",
  "decimal.js-light",
];

// The React runtime + router are on the first-paint critical path no matter
// what, but they change far less often than app code — a stable vendor chunk
// keeps them cached across app deploys.
const REACT_VENDOR = ["react", "react-dom", "react-router", "react-router-dom", "scheduler"];

/** Match a package as a path segment of a node_modules id (so "react" can't match "react-smooth"). */
function inPackage(id: string, pkgs: string[]): boolean {
  return pkgs.some((p) => id.includes(`/node_modules/${p}/`));
}

export default defineConfig({
  plugins: [react()],
  build: {
    // @novnc/novnc 1.7 uses top-level await; the default "modules" target rejects it.
    target: "es2022",
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("/node_modules/")) return undefined;
          if (inPackage(id, CHART_VENDOR)) return "charts-vendor";
          if (inPackage(id, REACT_VENDOR)) return "react-vendor";
          return undefined;
        },
      },
    },
  },
  optimizeDeps: { esbuildOptions: { target: "es2022" } },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: false,
      },
      "/ws": {
        target: "ws://127.0.0.1:8787",
        ws: true,
      },
    },
  },
});
