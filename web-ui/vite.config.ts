import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev server proxies the API + SSE to the FastAPI process (default :8765).
// In prod the built bundle is served by FastAPI itself, so the proxy is
// dev-only. Override the target with VITE_API_PROXY when needed.
const API_TARGET = process.env.VITE_API_PROXY ?? "http://127.0.0.1:8765";

// The `agent-team` mode builds the SPA for the agent_team plugin, which serves
// it under `/agent-team`. Assets must be referenced from that sub-path, and the
// output goes to a separate dir so it never clobbers the default build.
export default defineConfig(({ mode }) => {
  const isAgentTeam = mode === "agent-team";
  return {
    plugins: [react()],
    base: isAgentTeam ? "/agent-team/" : "/",
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: API_TARGET,
          changeOrigin: true,
          // SSE needs buffering disabled; the proxy streams chunks through.
          ws: false,
        },
      },
    },
    build: {
      outDir: isAgentTeam ? "dist-agent-team" : "dist",
      sourcemap: true,
    },
  };
});
