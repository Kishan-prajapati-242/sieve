/// <reference types="vitest/config" />
// Dev server lives inside the compose network (DECISION-1d: no host Node).
// Three settings exist only because of that:
//  - host: bind 0.0.0.0 so the container port mapping reaches Vite at all.
//  - watch.usePolling: inotify events from the host bind mount do not cross
//    the podman/Docker VM boundary (same reason uvicorn --reload is dead in
//    the api container, see docs/progress.md) — chokidar must poll.
//  - proxy: the browser talks only to :5173; Vite forwards /api to the api
//    service by compose DNS name. No CORS config on the backend, and the
//    deployed frontend (Cloudflare Pages, Phase 5) keeps the same relative
//    /api paths.
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    watch: { usePolling: true, interval: 300 },
    proxy: {
      "/api": { target: "http://api:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test-setup.ts"],
  },
});
