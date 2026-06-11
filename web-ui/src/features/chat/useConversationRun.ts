import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { toast } from "sonner";
import { useApi } from "@/api/ApiProvider";
import { qk } from "@/api/hooks";
import { attachRunStream, openRunStream, type RunStreamHandlers } from "@/api/sse";
import { ApiError } from "@/api/types";
import { type Plan, planFromApi, TODO_TOOL } from "./plan";
import {
  blocksFromHistory,
  initialRunState,
  runReducer,
} from "./reducer";
import type { UserAttachment } from "./types";

/**
 * Drives one conversation: loads history, opens the SSE run stream on
 * send, feeds events into the reducer, and exposes steer / cancel.
 */
export function useConversationRun(convId: string | undefined) {
  const { client, getToken } = useApi();
  const qc = useQueryClient();
  const [state, dispatch] = useReducer(runReducer, initialRunState);
  const [plan, setPlan] = useState<Plan | null>(null);
  const abortRef = useRef<(() => void) | null>(null);

  // Fetch the authoritative task list from the server (the backend TodoStore
  // owns merge/replace semantics, so we never reconstruct it client-side).
  const refreshPlan = useCallback(() => {
    if (!convId) return;
    void client
      .getTodos(convId)
      .then((dto) => setPlan(planFromApi(dto)))
      .catch(() => {
        /* no plan yet / new conversation */
      });
  }, [convId, client]);

  // Shared SSE handlers for both the initial run stream and re-attach.
  const makeHandlers = useCallback(
    (): RunStreamHandlers => ({
      onEvent: (event) => {
        // Synthetic frame from the auto-title side task: rename the thread
        // in the sidebar + header without a manual refetch.
        if ((event as { type?: string }).type === "conversation_title") {
          void qc.invalidateQueries({ queryKey: ["conversations"] });
          if (convId) {
            void qc.invalidateQueries({ queryKey: qk.conversation(convId) });
          }
          return;
        }
        dispatch({ type: "event", event });
        // Whenever the agent touches the task list, pull the fresh snapshot.
        if (event.type === "tool_use_end" && event.tool_name === TODO_TOOL) {
          refreshPlan();
        }
      },
      onError: (err) => {
        const message = err instanceof Error ? err.message : "Stream failed";
        dispatch({ type: "fatal", message });
        toast.error(message);
      },
      onClose: () => {
        dispatch({ type: "stopped" });
        abortRef.current = null;
        if (convId) {
          // Refresh persisted totals (cost/tokens/run count) in the sidebar.
          void qc.invalidateQueries({ queryKey: qk.conversation(convId) });
        }
        refreshPlan();
      },
    }),
    [convId, qc, refreshPlan],
  );

  // Load persisted history whenever the conversation changes — and if a
  // run is still streaming server-side (the user reloaded / dropped the
  // connection mid-stream), re-attach to it so the live stream resumes.
  useEffect(() => {
    let cancelled = false;
    abortRef.current?.();
    abortRef.current = null;
    setPlan(null);
    if (!convId) {
      dispatch({ type: "reset", blocks: [] });
      return;
    }
    dispatch({ type: "reset", blocks: [] });

    void (async () => {
      // Live truth about an in-flight run; drives both history dedupe and
      // whether we re-attach to the stream below.
      let activeRunId: string | null = null;
      try {
        const detail = await client.getConversation(convId);
        activeRunId = detail.active_run_id ?? null;
      } catch {
        /* conversation not loaded yet */
      }
      if (cancelled) return;

      try {
        const msgs = await client.listMessages(convId);
        if (!cancelled) {
          dispatch({ type: "reset", blocks: blocksFromHistory(msgs, activeRunId) });
        }
      } catch {
        /* empty / new conversation */
      }
      if (cancelled) return;

      if (activeRunId) {
        dispatch({ type: "start" });
        abortRef.current = attachRunStream(convId, getToken, makeHandlers());
      }
    })();

    refreshPlan();
    return () => {
      cancelled = true;
    };
  }, [convId, client, refreshPlan, getToken, makeHandlers]);

  // Tear down the stream on unmount.
  useEffect(() => () => abortRef.current?.(), []);

  const send = useCallback(
    (prompt: string, attachments: UserAttachment[] = []) => {
      if (!convId || state.running) return;
      if (!prompt.trim() && attachments.length === 0) return;
      dispatch({
        type: "user",
        text: prompt,
        attachments: attachments.length ? attachments : undefined,
      });
      dispatch({ type: "start" });
      // Stable id so a transient reconnect attaches to this run instead
      // of starting a duplicate (see sse.ts / RunHub.open).
      const runId =
        globalThis.crypto?.randomUUID?.() ?? `run-${Date.now()}-${Math.random()}`;
      abortRef.current = openRunStream(
        convId,
        prompt,
        runId,
        getToken,
        makeHandlers(),
        attachments.map((a) => a.id),
      );
    },
    [convId, state.running, getToken, makeHandlers],
  );

  const steer = useCallback(
    async (content: string, mode: "queue" | "interrupt" | "steer") => {
      if (!convId) return;
      try {
        await client.postMessage(convId, content, mode);
        toast.success(mode === "queue" ? "Message queued" : `Sent (${mode})`);
      } catch (err) {
        toast.error(err instanceof ApiError ? err.message : "Failed to send");
      }
    },
    [convId, client],
  );

  const cancel = useCallback(async () => {
    if (!convId) return;
    try {
      await client.cancelRun(convId);
    } catch {
      /* no active run */
    }
    abortRef.current?.();
    abortRef.current = null;
    dispatch({ type: "stopped" });
  }, [convId, client]);

  return {
    blocks: state.blocks,
    running: state.running,
    usage: state.usage,
    context: state.context,
    fatalError: state.fatalError,
    plan,
    send,
    steer,
    cancel,
  };
}
