import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Proxy `/voice/*` and `/traces/*` and `/healthz` to the FastAPI backend so
// the dev server doesn't need CORS plumbing. In prod the frontend is served
// behind the same domain as the backend, or `VITE_API_BASE` overrides.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5174,
    proxy: {
      "/voice": "http://localhost:8000",
      "/traces": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
      "/metrics": "http://localhost:8000",
    },
  },
});
