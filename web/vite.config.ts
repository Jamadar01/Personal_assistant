import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // The dev server runs on 5173 and the API on 8000. Proxying /api means the browser
    // only ever talks to one origin, so there is no CORS to configure - and the fetch
    // paths in the code stay the same once FastAPI serves the built files itself.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
