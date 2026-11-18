import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В разработке API — на бэкенде FastAPI (uvicorn на 8000)
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/media": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000",
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
