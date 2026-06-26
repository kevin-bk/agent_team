import { useCallback, useEffect, useReducer, useRef } from "react";
import { useApi } from "@/api/ApiProvider";
import { attachTaskRunStream, type RunStreamHandlers } from "@/api/sse";
import {
  blocksFromHistory,
  initialRunState,
  runReducer,
} from "@/features/chat/reducer";
import { Timeline } from "@/features/chat/Timeline";
import { Spinner } from "@/components/ui/spinner";

/**
 * The generator's continuous work transcript across every iteration, embedded
 * inline in the Goal panel so a human can see exactly what the agent did —
 * tools, reasoning and the live plan checklist — alongside the critic verdicts.
 *
 * Read-only: it loads the persisted transcript for the generator conversation
 * and, while the loop is running, re-attaches to the active generator run so
 * the work streams in live (surviving a reload like the chat does).
 */
export function GoalTranscript({
  taskId,
  conversationId,
  activeRunId,
  running,
}: {
  taskId: string;
  conversationId: string;
  /** Generator run streaming right now (the running iteration), if any. */
  activeRunId?: string | null;
  running: boolean;
}) {
  const { client, getToken } = useApi();
  const [state, dispatch] = useReducer(runReducer, initialRunState);
  const abortRef = useRef<(() => void) | null>(null);
  const runIdRef = useRef<string | null>(null);

  const reloadHistory = useCallback(async () => {
    try {
      const msgs = await client.listTaskAttemptMessages(
        taskId,
        "agent",
        conversationId,
      );
      dispatch({ type: "reset", blocks: blocksFromHistory(msgs, null) });
    } catch {
      /* keep whatever is on screen */
    }
  }, [client, taskId, conversationId]);

  const makeHandlers = useCallback(
    (): RunStreamHandlers => ({
      onEvent: (event) => dispatch({ type: "event", event }),
      onError: () => dispatch({ type: "stopped" }),
      onClose: () => {
        dispatch({ type: "stopped" });
        abortRef.current = null;
        runIdRef.current = null;
        // Reload so finalised turns gain their persisted shape (sender, etc).
        void reloadHistory();
      },
    }),
    [reloadHistory],
  );

  // Load the persisted transcript whenever the conversation changes, then
  // attach to the active generator run (loading it as live, replayed history).
  useEffect(() => {
    let cancelled = false;
    abortRef.current?.();
    abortRef.current = null;
    runIdRef.current = null;
    dispatch({ type: "reset", blocks: [] });
    void (async () => {
      try {
        const msgs = await client.listTaskAttemptMessages(
          taskId,
          "agent",
          conversationId,
        );
        if (!cancelled) {
          dispatch({
            type: "reset",
            // Skip the active run's persisted turns; they replay live below.
            blocks: blocksFromHistory(msgs, running ? activeRunId : null),
          });
        }
      } catch {
        /* empty transcript */
      }
      if (cancelled) return;
      if (running && activeRunId) {
        runIdRef.current = activeRunId;
        dispatch({ type: "start" });
        abortRef.current = attachTaskRunStream(
          activeRunId,
          getToken,
          makeHandlers(),
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId, conversationId, activeRunId, running, client, getToken, makeHandlers]);

  useEffect(() => () => abortRef.current?.(), []);

  if (state.blocks.length === 0 && !state.running) {
    return (
      <div className="flex items-center gap-2 px-4 py-6 text-[13px] text-muted-foreground">
        {running ? (
          <>
            <Spinner className="h-4 w-4" /> Waiting for the agent to start…
          </>
        ) : (
          "No work recorded for this goal yet."
        )}
      </div>
    );
  }

  return (
    <Timeline blocks={state.blocks} running={state.running} agentName="Agent" />
  );
}
