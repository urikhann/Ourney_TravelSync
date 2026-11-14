import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Windows Docker bind mounts can miss native file-change events.
    watch: { usePolling: true, interval: 500 },
  },
});
