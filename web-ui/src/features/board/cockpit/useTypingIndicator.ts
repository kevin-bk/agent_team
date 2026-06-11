import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApi } from "@/api/ApiProvider";
import { useMe } from "@/api/hooks";
import { useBoardEventListener } from "../BoardEventsContext";

/** Throttle between "start" pings while a user keeps typing (ms). */
const PING_THROTTLE_MS = 2500;
/**
 * Keep showing a typer for this long after their last "start" ping. Generous
 * (10s) so a short pause while composing doesn't flicker the indicator and it
 * lingers smoothly after they stop — better UX than cutting out instantly.
 */
const TYPER_TTL_MS = 10000;

/**
 * Collaborative typing presence for one (task × agent) thread.
 *
 * Receives `agent.typing` board events (excluding our own) and exposes the
 * names currently composing, expiring each on a TTL so a dropped "stop" frame
 * never leaves a stuck indicator. Also exposes throttled emitters the composer
 * calls on keystroke / blur / send.
 */
export function useTypingIndicator(
  taskId: string | undefined,
  agentId: string | undefined,
) {
  const { client } = useApi();
  const me = useMe();
  const myId = me.data?.user_id;

  const [typers, setTypers] = useState<Record<string, string>>({});
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const lastPing = useRef(0);

  const drop = useCallback((uid: string) => {
    const t = timers.current[uid];
    if (t) clearTimeout(t);
    delete timers.current[uid];
    setTypers((prev) => {
      if (!(uid in prev)) return prev;
      const next = { ...prev };
      delete next[uid];
      return next;
    });
  }, []);

  useBoardEventListener((e) => {
    if (e.type !== "agent.typing") return;
    if (e.task_id !== taskId || e.agent_id !== agentId) return;
    const uid = e.actor_id;
    if (!uid || uid === myId) return;
    if (e.state === "stop") {
      drop(uid);
      return;
    }
    const name = e.user_name || "Someone";
    setTypers((prev) => (prev[uid] === name ? prev : { ...prev, [uid]: name }));
    if (timers.current[uid]) clearTimeout(timers.current[uid]);
    timers.current[uid] = setTimeout(() => drop(uid), TYPER_TTL_MS);
  });

  // Reset state + clear timers when the thread changes or on unmount.
  useEffect(() => {
    return () => {
      for (const t of Object.values(timers.current)) clearTimeout(t);
      timers.current = {};
      lastPing.current = 0;
      setTypers({});
    };
  }, [taskId, agentId]);

  const notifyTyping = useCallback(() => {
    if (!taskId || !agentId) return;
    const now = Date.now();
    if (now - lastPing.current < PING_THROTTLE_MS) return;
    lastPing.current = now;
    void client.setTyping(taskId, agentId, "start").catch(() => {});
  }, [client, taskId, agentId]);

  const stopTyping = useCallback(() => {
    if (!taskId || !agentId) return;
    lastPing.current = 0;
    void client.setTyping(taskId, agentId, "stop").catch(() => {});
  }, [client, taskId, agentId]);

  const typingNames = useMemo(() => Object.values(typers), [typers]);
  return { typingNames, notifyTyping, stopTyping };
}
