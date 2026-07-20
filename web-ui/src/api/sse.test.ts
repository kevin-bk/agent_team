import { beforeEach, describe, expect, it, vi } from "vitest";

// Capture the handlers subscribeBoardEvents passes to fetch-event-source so we
// can drive its lifecycle callbacks (onopen/onclose/onerror) directly.
let handlers: Record<string, (...args: unknown[]) => unknown> = {};

vi.mock("@microsoft/fetch-event-source", () => ({
  EventStreamContentType: "text/event-stream",
  fetchEventSource: (_url: string, opts: Record<string, unknown>) => {
    handlers = opts as typeof handlers;
    return new Promise(() => {}); // never resolves; caller aborts
  },
}));

vi.mock("./config", () => ({ apiUrl: (p: string) => p }));

import { subscribeBoardEvents } from "./sse";

describe("subscribeBoardEvents reconnect", () => {
  beforeEach(() => {
    handlers = {};
  });

  it("retries (does not give up) when the server closes the stream with a clean EOF", () => {
    const abort = subscribeBoardEvents("b1", async () => "tok", () => {});
    // A graceful server shutdown / proxy EOF calls onclose, NOT onerror. It must
    // throw so the error is routed into the retry path instead of resolving.
    expect(() => handlers.onclose()).toThrow();
    // onerror returns a numeric backoff delay → the library keeps reconnecting.
    const delay = handlers.onerror(new Error("closed"));
    expect(typeof delay).toBe("number");
    expect(delay as number).toBeGreaterThan(0);
    abort();
  });

  it("fires onReconnect when the stream re-opens after a drop", async () => {
    const onReconnect = vi.fn();
    const abort = subscribeBoardEvents("b1", async () => "tok", () => {}, onReconnect);
    // First open: fresh connection, no reconnect callback.
    await handlers.onopen({
      ok: true,
      headers: { get: () => "text/event-stream" },
    });
    expect(onReconnect).not.toHaveBeenCalled();
    // Drop, then re-open → onReconnect fires so callers can refetch missed state.
    handlers.onerror(new Error("drop"));
    await handlers.onopen({
      ok: true,
      headers: { get: () => "text/event-stream" },
    });
    expect(onReconnect).toHaveBeenCalledTimes(1);
    abort();
  });

  it("does not throw from onclose once the caller has aborted", () => {
    const abort = subscribeBoardEvents("b1", async () => "tok", () => {});
    abort();
    expect(() => handlers.onclose()).not.toThrow();
  });
});
