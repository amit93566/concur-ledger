import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API stays on :8000. In the image, FastAPI serves this build
// at /dashboard, so requests are same-origin and need no CORS.
const api = "http://localhost:8000";
const routes = ["/whoami", "/datasets", "/holds", "/audit", "/config", "/health", "/adapters", "/admin", "/debug", "/reservations"];

export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  server: { proxy: Object.fromEntries(routes.map((r) => [r, api])) },
});
