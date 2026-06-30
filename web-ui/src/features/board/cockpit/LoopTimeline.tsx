/**
 * The Goal's *merged* work transcript: planning, building and every critic
 * iteration woven into one chronological timeline (rather than one conversation
 * at a time). Each turn is tagged with the role that produced it — Planner,
 * Builder or Critic — and drawn against a vertical time rail, so a human can
 * read the whole run top-to-bottom like a story.
 */
import {
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ClipboardList,
  Hammer,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { useApi } from "@/api/ApiProvider";
import { attachTaskRunStream, type RunStreamHandlers } from "@/api/sse";
import {
  blocksFromHistory,
  initialRunState,
  runReducer,
} from "@/features/chat/reducer";
import type { Block } from "@/features/chat/types";
import { ToolCard } from "@/features/chat/ToolCard";
import { TODO_TOOL } from "@/features/chat/plan";
import { Markdown } from "@/components/Markdown";
import { Brain, CheckCircle2, Circle, ListChecks } from "@/components/icons";
import { Spinner } from "@/components/ui/spinner";
import { formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";
import { AgentLogo, type ResolvedAgent } from "./agentRoles";
import type { LoopInfoDTO } from "@/api/types";

export type RoleKind = "plan" | "build" | "critic";

export interface RoleMeta {
  /** Stable unique key (e.g. "plan", "build", "critic-<attemptId>"). */
  key: string;
  /** Human label shown on the turn ("Planner", "Builder", "Critic #2"). */
  label: string;
  kind: RoleKind;
  conversationId: string;
}

interface TaggedBlock {
  role: RoleMeta;
  block: Block;
}

const ROLE_STYLE: Record<
  RoleKind,
  { icon: LucideIcon; badge: string; dot: string; name: string }
> = {
  plan: {
    icon: ClipboardList,
    badge:
      "bg-sky-500/15 text-sky-600 ring-1 ring-inset ring-sky-500/30 dark:text-sky-300",
    dot: "bg-sky-500",
    name: "text-sky-700 dark:text-sky-300",
  },
  build: {
    icon: Hammer,
    badge:
      "bg-emerald-500/15 text-emerald-600 ring-1 ring-inset ring-emerald-500/30 dark:text-emerald-300",
    dot: "bg-emerald-500",
    name: "text-emerald-700 dark:text-emerald-300",
  },
  critic: {
    icon: ShieldCheck,
    badge:
      "bg-violet-500/15 text-violet-600 ring-1 ring-inset ring-violet-500/30 dark:text-violet-300",
    dot: "bg-violet-500",
    name: "text-violet-700 dark:text-violet-300",
  },
};

// Writes/edits are expanded up-front so their code or diff is visible without a
// click. Matched case-insensitively (CLI engines capitalise tool names).
const WRITE_OR_EDIT = new Set([
  "write_file",
  "write",
  "create_file",
  "create",
  "edit",
  "edit_file",
  "str_replace",
  "str_replace_editor",
  "multiedit",
]);

/** The plan / build / critic conversations that make up a goal, in order. */
export function buildRoleSources(info: LoopInfoDTO): RoleMeta[] {
  const out: RoleMeta[] = [];
  if (info.planner_conversation_id) {
    out.push({
      key: "plan",
      label: "Planner",
      kind: "plan",
      conversationId: info.planner_conversation_id,
    });
  }
  if (info.generator_conversation_id) {
    out.push({
      key: "build",
      label: "Builder",
      kind: "build",
      conversationId: info.generator_conversation_id,
    });
  }
  for (const a of info.attempts) {
    const conv =
      a.critic_conversation_id ??
      a.evaluations.find((e) => e.conversation_id)?.conversation_id;
    if (conv) {
      out.push({
        key: `critic-${a.id}`,
        label: `Critic #${a.attempt_no}`,
        kind: "critic",
        conversationId: conv,
      });
    }
  }
  return out;
}

/**
 * Load every role's persisted transcript, merge by timestamp into one stream,
 * and — while the loop runs — splice the active role's live SSE blocks onto the
 * end (the running role is always the most recent).
 */
export function useGoalActivity(
  taskId: string,
  info: LoopInfoDTO,
  running: boolean,
) {
  const { client, getToken } = useApi();
  const sources = useMemo(() => buildRoleSources(info), [info]);
  const activeConv = info.active_conversation_id ?? null;
  const activeRunId = info.active_run_id ?? null;
  const activeRole = useMemo(
    () => sources.find((s) => s.conversationId === activeConv) ?? null,
    [sources, activeConv],
  );

  const [history, setHistory] = useState<TaggedBlock[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const [liveState, dispatch] = useReducer(runReducer, initialRunState);
  const abortRef = useRef<(() => void) | null>(null);

  // Persisted history for every role, merged chronologically. The active run's
  // turns are skipped for its own conversation (they replay live below).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const all: TaggedBlock[] = [];
      for (const src of sources) {
        try {
          const msgs = await client.listTaskAttemptMessages(
            taskId,
            "agent",
            src.conversationId,
          );
          const skip =
            running && src.conversationId === activeConv ? activeRunId : null;
          for (const block of blocksFromHistory(msgs, skip)) {
            all.push({ role: src, block });
          }
        } catch {
          /* skip a role we can't load */
        }
      }
      all.sort(
        (x, y) => (x.block.createdAtMs ?? 0) - (y.block.createdAtMs ?? 0),
      );
      if (!cancelled) setHistory(all);
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId, sources, running, activeConv, activeRunId, client, reloadKey]);

  // Live attach to the running role's run.
  useEffect(() => {
    abortRef.current?.();
    abortRef.current = null;
    dispatch({ type: "reset", blocks: [] });
    if (!running || !activeRunId || !activeRole) return;
    dispatch({ type: "start" });
    const handlers: RunStreamHandlers = {
      onEvent: (event) => dispatch({ type: "event", event }),
      onError: () => dispatch({ type: "stopped" }),
      onClose: () => {
        dispatch({ type: "stopped" });
        abortRef.current = null;
        // Re-pull persisted history so the finished turns gain their final shape.
        setReloadKey((k) => k + 1);
      },
    };
    abortRef.current = attachTaskRunStream(activeRunId, getToken, handlers);
    return () => {
      abortRef.current?.();
      abortRef.current = null;
    };
  }, [running, activeRunId, activeRole, getToken]);

  useEffect(() => () => abortRef.current?.(), []);

  const items = useMemo<TaggedBlock[]>(() => {
    const live: TaggedBlock[] = activeRole
      ? liveState.blocks.map((block) => ({ role: activeRole, block }))
      : [];
    return [...history, ...live];
  }, [history, liveState.blocks, activeRole]);

  return { items, sources, streaming: liveState.running || running };
}

interface LoopTurn {
  key: string;
  role: RoleMeta;
  startMs?: number;
  blocks: Block[];
}

/** A turn starts on each spoken (assistant/user) block or whenever the role
 * changes; trailing tool/thinking blocks attach to the utterance above them. */
function groupTurns(items: TaggedBlock[]): LoopTurn[] {
  const turns: LoopTurn[] = [];
  for (const it of items) {
    const prev = turns[turns.length - 1];
    const startsNew =
      !prev ||
      prev.role.key !== it.role.key ||
      it.block.kind === "user" ||
      it.block.kind === "assistant";
    if (startsNew) {
      turns.push({
        key: it.block.id,
        role: it.role,
        startMs: it.block.createdAtMs,
        blocks: [it.block],
      });
    } else {
      prev.blocks.push(it.block);
    }
  }
  return turns;
}

export function LoopTimeline({
  items,
  streaming,
  onOpenFile,
  roleAgents,
}: {
  items: TaggedBlock[];
  streaming: boolean;
  onOpenFile?: (path: string) => void;
  /** Which AI staffs each role, so a turn can name its agent (Codex / Claude…). */
  roleAgents?: Partial<Record<RoleKind, ResolvedAgent | null>>;
}) {
  const turns = useMemo(() => groupTurns(items), [items]);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, streaming]);

  if (turns.length === 0) {
    return (
      <div className="flex items-center gap-2 px-4 py-10 text-[13px] text-muted-foreground">
        {streaming ? (
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
    <div className="px-1 py-3 sm:px-2">
      {turns.map((turn) => (
        <TurnRow
          key={turn.key}
          turn={turn}
          onOpenFile={onOpenFile}
          agent={roleAgents?.[turn.role.kind] ?? null}
        />
      ))}
      {streaming && (
        <div className="flex items-center gap-3">
          <div className="w-14 shrink-0" />
          <div className="relative flex w-6 shrink-0 justify-center">
            <span className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-border" />
          </div>
          <div className="flex items-center gap-2 pb-6 pl-1 text-[12px] text-muted-foreground">
            <Spinner className="h-3.5 w-3.5" /> streaming…
          </div>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}

function TurnRow({
  turn,
  onOpenFile,
  agent,
}: {
  turn: LoopTurn;
  onOpenFile?: (path: string) => void;
  agent?: ResolvedAgent | null;
}) {
  const style = ROLE_STYLE[turn.role.kind];
  const Icon = style.icon;
  return (
    <div className="flex gap-3">
      <time className="w-14 shrink-0 pt-1.5 text-right font-mono text-[10.5px] leading-5 text-muted-foreground/70 tabular-nums">
        {turn.startMs ? formatTimestamp(turn.startMs) : ""}
      </time>
      {/* Time rail: a continuous line with the role node centred on it. */}
      <div className="relative flex w-6 shrink-0 justify-center">
        <span className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-border" />
        <span
          className={cn(
            "relative z-10 mt-0.5 flex h-6 w-6 items-center justify-center rounded-full",
            style.badge,
          )}
        >
          <Icon className="h-3.5 w-3.5" strokeWidth={2.25} />
        </span>
      </div>
      <div className="min-w-0 flex-1 pb-6">
        <div className="mb-1.5 flex items-center gap-1.5">
          <span className={cn("text-[12px] font-semibold", style.name)}>
            {turn.role.label}
          </span>
          {agent && (
            <span className="inline-flex items-center gap-1 rounded-full border border-border bg-card/70 px-1.5 py-px text-[10.5px] text-muted-foreground">
              <AgentLogo
                alias={agent.alias}
                model={agent.model}
                className="h-3.5 w-3.5"
                glyphClassName="h-2.5 w-2.5"
              />
              {agent.name}
            </span>
          )}
        </div>
        <div className="space-y-2">
          {turn.blocks.map((b) => (
            <BlockView key={b.id} block={b} onOpenFile={onOpenFile} />
          ))}
        </div>
      </div>
    </div>
  );
}

function BlockView({
  block,
  onOpenFile,
}: {
  block: Block;
  onOpenFile?: (path: string) => void;
}) {
  switch (block.kind) {
    case "user":
    case "assistant":
      return <AssistantText text={block.text} />;
    case "thinking":
      return <Reasoning text={block.text} />;
    case "tool":
      if (block.name === TODO_TOOL) return null;
      return (
        <ToolCard
          block={block}
          onOpenFile={onOpenFile}
          defaultOpen={WRITE_OR_EDIT.has(block.name.toLowerCase())}
        />
      );
    case "plan":
      return <PlanChecklist entries={block.entries} />;
    case "notice":
      return (
        <div className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card/60 px-2.5 py-1 text-[11px] text-muted-foreground">
          {block.text}
        </div>
      );
    default:
      return null;
  }
}

type Segment = { type: "text" | "thinking"; content: string };

/** Split out inline `<thinking>` so reasoning collapses behind a disclosure. */
function splitThinking(text: string): Segment[] {
  const segments: Segment[] = [];
  const pair = /<think(?:ing)?>([\s\S]*?)<\/think(?:ing)?>/gi;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = pair.exec(text)) !== null) {
    if (m.index > last)
      segments.push({ type: "text", content: text.slice(last, m.index) });
    segments.push({ type: "thinking", content: m[1] });
    last = m.index + m[0].length;
  }
  const rest = text.slice(last);
  const open = rest.search(/<think(?:ing)?>/i);
  if (open !== -1) {
    if (open > 0) segments.push({ type: "text", content: rest.slice(0, open) });
    segments.push({
      type: "thinking",
      content: rest.slice(open).replace(/<think(?:ing)?>/i, ""),
    });
  } else if (rest) {
    segments.push({ type: "text", content: rest });
  }
  return segments;
}

function AssistantText({ text }: { text: string }): ReactNode {
  const segments = useMemo(() => splitThinking(text), [text]);
  const hasVisible = segments.some(
    (s) => s.type === "thinking" || s.content.trim(),
  );
  if (!hasVisible) return null;
  return (
    <div className="space-y-2">
      {segments.map((seg, i) =>
        seg.type === "thinking" ? (
          <Reasoning key={i} text={seg.content.trim()} />
        ) : seg.content.trim() ? (
          <div
            key={i}
            className="prose-chat max-w-none text-[13px] leading-relaxed text-foreground"
          >
            <Markdown>{seg.content}</Markdown>
          </div>
        ) : null,
      )}
    </div>
  );
}

function Reasoning({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (!text.trim()) return null;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
      >
        <Brain className="h-3.5 w-3.5" />
        {open ? "Hide reasoning" : "Show reasoning"}
      </button>
      {open && (
        <div className="mt-1 whitespace-pre-wrap rounded-lg border border-dashed border-border bg-card/40 p-3 text-[12px] italic text-muted-foreground">
          {text}
        </div>
      )}
    </div>
  );
}

function PlanChecklist({
  entries,
}: {
  entries: { title: string; status: "todo" | "in_progress" | "done" }[];
}) {
  if (!entries.length) return null;
  const done = entries.filter((e) => e.status === "done").length;
  return (
    <div className="rounded-lg border border-border bg-card/60 p-2.5">
      <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-medium text-muted-foreground">
        <ListChecks className="h-3.5 w-3.5" /> Plan
        <span className="ml-auto tabular-nums">
          {done}/{entries.length}
        </span>
      </div>
      <ul className="space-y-1">
        {entries.map((e, i) => (
          <li key={i} className="flex items-start gap-2 text-[12.5px]">
            {e.status === "done" ? (
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
            ) : e.status === "in_progress" ? (
              <Spinner className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-500" />
            ) : (
              <Circle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground/50" />
            )}
            <span
              className={cn(
                "leading-snug",
                e.status === "done"
                  ? "text-muted-foreground line-through"
                  : e.status === "in_progress"
                    ? "font-medium text-foreground"
                    : "text-foreground",
              )}
            >
              {e.title}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
