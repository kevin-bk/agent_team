import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { toast } from "sonner";
import { useApi } from "@/api/ApiProvider";
import { qk } from "@/api/hooks";
import { attachTaskRunStream, type RunStreamHandlers } from "@/api/sse";
import { ApiError } from "@/api/types";
import {
  blocksFromHistory,
  initialRunState,
  runReducer,
} from "@/features/chat/reducer";
import type { UserAttachment } from "@/features/chat/types";
import type { TaskRunDTO } from "@/api/types";
import { useBoardEventListener } from "../BoardEventsContext";

/** Persisted CLI usage restored from the latest finished run (survives reload). */
interface PersistedUsage {
  /** Context-window gauge text, e.g. "45,000/200,000 tokens". */
  gauge: string | null;
  /** Cumulative total tokens for the conversation (latest run's value). */
  total: number | null;
}

const NO_PERSISTED_USAGE: PersistedUsage = { gauge: null, total: null };

/**
 * Pick the conversation's usage readout from its runs (newest first).
 *
 * Direct CLI token totals are *cumulative across the session*, so the latest
 * run already holds the whole-conversation total — we take it rather than
 * summing. The gauge string and total may come from different runs (a later
 * run might lack one), so each is resolved independently.
 */
function persistedUsageFromRuns(runs: TaskRunDTO[]): PersistedUsage {
  let gauge: string | null = null;
  let total: number | null = null;
  for (const run of runs) {
    if (gauge === null && run.cli_usage_text) gauge = run.cli_usage_text;
    if (total === null) {
      const t = run.total_tokens ?? run.tokens ?? 0;
      if (t > 0) total = t;
    }
    if (gauge !== null && total !== null) break;
  }
  return { gauge, total };
}

/**
 * Drives one (task × agent) conversation thread in the cockpit.
 *
 * Mirrors `useConversationRun` but for task mentions: the mention is a JSON
 * POST that returns a run id, and the live trajectory is streamed by run id
 * (`/api/runs/{id}/events`). On mount it loads the persisted transcript and,
 * if a run is still streaming server-side (after a reload / drop), re-attaches
 * so the live stream resumes — surviving an F5 just like the chat.
 */
export function useTaskAgentRun(
  taskId: string | undefined,
  agentId: string | undefined,
) {
  const { client, getToken } = useApi();
  const qc = useQueryClient();
  const [state, dispatch] = useReducer(runReducer, initialRunState);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [persistedUsage, setPersistedUsage] =
    useState<PersistedUsage>(NO_PERSISTED_USAGE);
  const abortRef = useRef<(() => void) | null>(null);
  const runIdRef = useRef<string | null>(null);

  const makeHandlers = useCallback(
    (): RunStreamHandlers => ({
      onEvent: (event) => dispatch({ type: "event", event }),
      onError: (err) => {
        const message = err instanceof Error ? err.message : "Stream failed";
        dispatch({ type: "fatal", message });
        toast.error(message);
      },
      onClose: () => {
        dispatch({ type: "stopped" });
        abortRef.current = null;
        runIdRef.current = null;
        if (taskId && agentId) {
          void qc.invalidateQueries({
            queryKey: qk.taskMessages(taskId, agentId),
          });
          void qc.invalidateQueries({ queryKey: qk.taskRuns(taskId, agentId) });
          void qc.invalidateQueries({ queryKey: qk.taskRuns(taskId) });
          void qc.invalidateQueries({ queryKey: qk.taskFileTree(taskId, "") });
          // Reload the persisted transcript so the just-finalised turns gain
          // their sender attribution (avatar + name) — same as an F5 — instead
          // of leaving the optimistic, sender-less live blocks on screen.
          void (async () => {
            try {
              const msgs = await client.listTaskAgentMessages(taskId, agentId);
              dispatch({ type: "reset", blocks: blocksFromHistory(msgs, null) });
            } catch {
              /* keep the live blocks if the reload fails */
            }
          })();
          // Restore the CLI usage readout from the now-finalised run so the
          // header keeps showing tokens after the live ``cliUsage`` is cleared.
          void (async () => {
            try {
              const runs = await client.listTaskRuns(taskId, agentId);
              setPersistedUsage(persistedUsageFromRuns(runs));
            } catch {
              /* keep whatever was already shown */
            }
          })();
        }
      },
    }),
    [taskId, agentId, qc, client],
  );

  // Load transcript + re-attach to any in-flight run when the pair changes.
  useEffect(() => {
    let cancelled = false;
    abortRef.current?.();
    abortRef.current = null;
    runIdRef.current = null;
    dispatch({ type: "reset", blocks: [] });
    setPersistedUsage(NO_PERSISTED_USAGE);
    if (!taskId || !agentId) {
      setLoadingHistory(false);
      return;
    }
    setLoadingHistory(true);

    void (async () => {
      let activeRunId: string | null = null;
      try {
        const runs = await client.listTaskRuns(taskId, agentId);
        const active = runs.find(
          (r) => r.status === "running" || r.status === "queued",
        );
        activeRunId = active?.id ?? null;
        if (!cancelled) setPersistedUsage(persistedUsageFromRuns(runs));
      } catch {
        /* no runs yet */
      }
      if (cancelled) return;

      try {
        const msgs = await client.listTaskAgentMessages(taskId, agentId);
        if (!cancelled) {
          dispatch({
            type: "reset",
            blocks: blocksFromHistory(msgs, activeRunId),
          });
        }
      } catch {
        /* empty thread */
      } finally {
        if (!cancelled) setLoadingHistory(false);
      }
      if (cancelled) return;

      if (activeRunId) {
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
  }, [taskId, agentId, client, getToken, makeHandlers]);

  useEffect(() => () => abortRef.current?.(), []);

  // Another user mentioned the same agent on this task: attach to *their* run
  // so we watch it live too (the shared thread is collaborative).
  useBoardEventListener((e) => {
    if (e.type !== "run.started") return;
    if (e.task_id !== taskId || e.agent_id !== agentId) return;
    if (!e.run_id || e.run_id === runIdRef.current) return;
    if (state.running) return;
    runIdRef.current = e.run_id;
    dispatch({ type: "start" });
    abortRef.current?.();
    abortRef.current = attachTaskRunStream(e.run_id, getToken, makeHandlers());
  });

  const send = useCallback(
    async (body: string, attachments: UserAttachment[] = []) => {
      if (!taskId || !agentId || state.running) return;
      if (!body.trim() && attachments.length === 0) return;
      dispatch({
        type: "user",
        text: body,
        attachments: attachments.length ? attachments : undefined,
      });
      dispatch({ type: "start" });
      try {
        const res = await client.mentionAgent(taskId, {
          agent_id: agentId,
          body,
          attachment_ids: attachments.map((a) => a.id),
        });
        runIdRef.current = res.run.id;
        abortRef.current = attachTaskRunStream(
          res.run.id,
          getToken,
          makeHandlers(),
        );
        void qc.invalidateQueries({ queryKey: qk.taskRuns(taskId, agentId) });
        void qc.invalidateQueries({ queryKey: qk.taskRuns(taskId) });
      } catch (err) {
        const message = err instanceof ApiError ? err.message : "Mention failed";
        dispatch({ type: "fatal", message });
        toast.error(message);
      }
    },
    [taskId, agentId, state.running, client, getToken, makeHandlers, qc],
  );

  const cancel = useCallback(async () => {
    const runId = runIdRef.current;
    if (runId) {
      try {
        await client.cancelTaskRun(runId);
      } catch {
        /* no active run */
      }
    }
    abortRef.current?.();
    abortRef.current = null;
    dispatch({ type: "stopped" });
  }, [client]);

  return {
    blocks: state.blocks,
    running: state.running,
    loadingHistory,
    usage: state.usage,
    // Live gauge while streaming; otherwise the value persisted on the last run.
    cliUsage: state.cliUsage ?? persistedUsage.gauge,
    // Cumulative conversation total (from the latest finished run).
    totalTokens: persistedUsage.total,
    fatalError: state.fatalError,
    send,
    cancel,
  };
}
