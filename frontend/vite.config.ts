import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
const backend = "http://127.0.0.1:9018";

export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: {
    outDir: "../src/herbfold/web",
    emptyOutDir: true,
    sourcemap: false,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "molecular-engine",
              test: /node_modules\/(three|@react-three|three-stdlib)/,
            },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: backend,
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (outgoing, incoming) => {
            const origin = incoming.headers.origin;
            // A same-origin browser call to this loopback dev server remains same-origin
            // at the backend. Foreign origins are preserved and rejected by the API.
            if (origin) {
              try {
                if (new URL(origin).host === incoming.headers.host)
                  outgoing.setHeader("origin", backend);
              } catch {
                /* Backend rejects malformed origins. */
              }
            }
          });
        },
      },
      "/static": { target: backend, changeOrigin: true },
    },
  },
});
