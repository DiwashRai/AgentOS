import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const backend = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    // "localhost" can resolve to ::1 only on Windows; bind IPv4 loopback explicitly.
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": backend, "/worker.py": backend },
  },
  // jsdom is slow to boot; component tests opt in with `// @vitest-environment jsdom`.
  test: { environment: "node" },
});
