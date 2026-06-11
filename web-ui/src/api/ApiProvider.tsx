import { useAuth } from "@clerk/clerk-react";
import { createContext, useContext, useMemo, type ReactNode } from "react";
import { ApiClient, type TokenGetter } from "./client";
import { AUTH_MODE } from "./config";

interface ApiContextValue {
  client: ApiClient;
  getToken: TokenGetter;
}

const ApiContext = createContext<ApiContextValue | null>(null);

/** Clerk-backed provider: bearer token fetched fresh per request. */
function ClerkApiProvider({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();

  const value = useMemo<ApiContextValue>(() => {
    const tokenGetter: TokenGetter = () => getToken();
    return { client: new ApiClient(tokenGetter), getToken: tokenGetter };
  }, [getToken]);

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

/**
 * Session-cookie provider: no bearer token. Auth rides on the host's
 * same-origin session cookie, so the token getter always resolves to null.
 */
function SessionApiProvider({ children }: { children: ReactNode }) {
  const value = useMemo<ApiContextValue>(() => {
    const tokenGetter: TokenGetter = async () => null;
    return { client: new ApiClient(tokenGetter), getToken: tokenGetter };
  }, []);

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

export const ApiProvider =
  AUTH_MODE === "session" ? SessionApiProvider : ClerkApiProvider;

export function useApi(): ApiContextValue {
  const ctx = useContext(ApiContext);
  if (!ctx) throw new Error("useApi must be used within <ApiProvider>");
  return ctx;
}
