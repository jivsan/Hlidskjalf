import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // @novnc/novnc 1.7 uses top-level await; the default "modules" target rejects it.
  build: { target: "es2022" },
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
