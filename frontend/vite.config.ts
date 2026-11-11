import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the single Groundwork backend runs on :8000; in production it serves this build itself.
export default defineConfig({
  plugins: [react()],
  // mermaid is code-split and loaded only when a diagram is shown, so its large chunks never block first paint
  build: { outDir: "dist", sourcemap: false, target: "es2020", chunkSizeWarningLimit: 1600 },
  server: { port: 5173, proxy: { "/api": { target: process.env.GROUNDWORK_API ?? "http://127.0.0.1:8000", changeOrigin: false } } },
});
