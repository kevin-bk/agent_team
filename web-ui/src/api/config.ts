/**
 * Build-time configuration so one FE codebase can serve multiple backends.
 *
 * - `API_BASE` lets a host mount the same SPA under a different API prefix
 *   (deep-agent serves `/api`; the agent_team plugin serves `/api/agent-team`).
 * - `AUTH_MODE` switches the auth strategy: `clerk` uses Clerk bearer tokens
 *   (default), `session` relies on the host's same-origin session cookie and
 *   sends no Authorization header.
 *
 * Both are read once at module load from Vite env (`.env.<mode>`), so they are
 * inlined at build time and add no runtime cost.
 */

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

/**
 * Router basename for when the SPA is mounted under a sub-path (e.g. the
 * agent_team plugin serves it at `/agent-team`). Defaults to root.
 */
export const ROUTER_BASE: string = import.meta.env.VITE_ROUTER_BASE ?? "/";

export type AuthMode = "clerk" | "session";

export const AUTH_MODE: AuthMode =
  import.meta.env.VITE_AUTH_MODE === "session" ? "session" : "clerk";

/**
 * Rewrite an absolute `/api/...` path onto the configured API base. Callers keep
 * writing canonical `/api/...` paths; this swaps the prefix when the SPA runs
 * under a non-default base. Non-`/api` URLs are returned untouched.
 */
export function apiUrl(path: string): string {
  if (API_BASE === "/api") return path;
  return path.startsWith("/api") ? API_BASE + path.slice("/api".length) : path;
}
