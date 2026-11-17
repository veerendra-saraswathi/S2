import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev server proxies /api to the ontology engine, so the browser talks to one
// origin and the API needs no CORS configuration.
const proxy = {
  "/api": {
    target: process.env.S2_API_URL ?? "http://localhost:8000",
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The map page is its own chunk (MapLibre, about 1 MB), loaded only when opened.
  build: { chunkSizeWarningLimit: 1100 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
  server: {
    port: 5173,
    proxy,
  },
  // `vite preview` (the container image) serves the build with the same proxy.
  preview: { port: 5173, proxy },
});
