import {
  ClerkProvider,
  RedirectToSignIn,
  SignedIn,
  SignedOut,
} from "@clerk/clerk-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { ApiProvider } from "@/api/ApiProvider";
import { AUTH_MODE, ROUTER_BASE } from "@/api/config";
import { App } from "@/App";
// Self-hosted free fallbacks for Claude's commercial type (Styrene B /
// Tiempos / Copernicus). The real names are listed first in the font
// stack (tailwind.config.ts) so a licensed install wins automatically.
import "@fontsource-variable/hanken-grotesk";
import "@fontsource-variable/source-serif-4";
import "@fontsource-variable/jetbrains-mono";
// Inter — the canonical Swiss/Linear product-UI face. Scoped to the board
// trial via the `.font-ui` utility (index.css) before rolling out app-wide.
import "@fontsource-variable/inter";
import "@/index.css";

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

const routerBasename = ROUTER_BASE === "/" ? undefined : ROUTER_BASE;

/**
 * Session-cookie host (e.g. the agent_team plugin): the host gates access via
 * its own login + session cookie, so we render the app directly without Clerk.
 */
function SessionRoot() {
  return (
    <QueryClientProvider client={queryClient}>
      <ApiProvider>
        <BrowserRouter basename={routerBasename}>
          <App />
        </BrowserRouter>
      </ApiProvider>
      <Toaster theme="system" position="top-center" richColors />
    </QueryClientProvider>
  );
}

/** Clerk-hosted deployment: sign-in is enforced by Clerk. */
function ClerkRoot() {
  if (!PUBLISHABLE_KEY) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-sm text-destructive">
        Missing <code className="mx-1">VITE_CLERK_PUBLISHABLE_KEY</code>. Set it
        in <code className="mx-1">web-ui/.env</code> and restart the dev server.
      </div>
    );
  }
  return (
    <ClerkProvider publishableKey={PUBLISHABLE_KEY} afterSignOutUrl="/">
      <SignedIn>
        <QueryClientProvider client={queryClient}>
          <ApiProvider>
            <BrowserRouter basename={routerBasename}>
              <App />
            </BrowserRouter>
          </ApiProvider>
        </QueryClientProvider>
      </SignedIn>
      <SignedOut>
        <RedirectToSignIn />
      </SignedOut>
      <Toaster theme="system" position="top-center" richColors />
    </ClerkProvider>
  );
}

function Root() {
  return AUTH_MODE === "session" ? <SessionRoot /> : <ClerkRoot />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
