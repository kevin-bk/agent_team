import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useRef,
} from "react";
import { useApi } from "@/api/ApiProvider";
import { qk } from "@/api/hooks";
import { type BoardEvent, subscribeBoardEvents } from "@/api/sse";

type Listener = (e: BoardEvent) => void;

interface BoardEventsCtx {
  subscribe: (fn: Listener) => () => void;
}

const Ctx = createContext<BoardEventsCtx | null>(null);

/**
 * Opens ONE realtime SSE connection for a board and:
 *  - centrally invalidates the React-Query caches affected by each event
 *    (board tasks, task comments, runs, messages) so every open view
 *    auto-refreshes when another user changes something;
 *  - re-broadcasts events to imperative listeners (e.g. the agent run hook,
 *    which needs to *attach* to a run another user just started).
 */
export function BoardEventsProvider({
  boardId,
  children,
}: {
  boardId: string;
  children: ReactNode;
}) {
  const { getToken } = useApi();
  const qc = useQueryClient();
  const listeners = useRef<Set<Listener>>(new Set());

  useEffect(() => {
    if (!boardId) return;
    return subscribeBoardEvents(boardId, getToken, (e) => {
      switch (e.type) {
        case "task.created":
        case "task.updated":
        case "task.moved":
        case "task.deleted":
          void qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) });
          if (e.task_id)
            void qc.invalidateQueries({
              queryKey: qk.taskActivity(e.task_id),
            });
          break;
        case "comment.created":
        case "comment.updated":
        case "comment.deleted":
          if (e.task_id)
            void qc.invalidateQueries({
              queryKey: qk.taskComments(e.task_id),
            });
          break;
        case "run.started":
        case "run.finished":
          if (e.task_id) {
            void qc.invalidateQueries({ queryKey: qk.taskRuns(e.task_id) });
            if (e.agent_id) {
              void qc.invalidateQueries({
                queryKey: qk.taskRuns(e.task_id, e.agent_id),
              });
              void qc.invalidateQueries({
                queryKey: qk.taskMessages(e.task_id, e.agent_id),
              });
            }
          }
          break;
      }
      for (const fn of listeners.current) fn(e);
    });
  }, [boardId, getToken, qc]);

  const ctx = useRef<BoardEventsCtx>({
    subscribe: (fn) => {
      listeners.current.add(fn);
      return () => {
        listeners.current.delete(fn);
      };
    },
  });

  return <Ctx.Provider value={ctx.current}>{children}</Ctx.Provider>;
}

/** Register an imperative listener for board events (no-op without a provider). */
export function useBoardEventListener(fn: Listener) {
  const ctx = useContext(Ctx);
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!ctx) return;
    return ctx.subscribe((e) => ref.current(e));
  }, [ctx]);
}
