import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backendOrigin = process.env.EI_API_ORIGIN ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": backendOrigin,
    },
  },
});
