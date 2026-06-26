import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  Circle,
  Clock,
  Download,
  FileText,
  GitBranch,
  Info,
  ListChecks,
  Scissors,
  Sparkles,
} from "@/components/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { AuthedImage } from "@/components/AuthedImage";
import { Badge } from "@/components/ui/badge";
import { Markdown } from "@/components/Markdown";
import { Spinner } from "@/components/ui/spinner";
import {
  formatBytes,
  formatDuration,
  formatTimestamp,
  formatTokens,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import { TODO_TOOL } from "./plan";
import { ToolCard } from "./ToolCard";
import type { Block, Sender, UserAttachment } from "./types";
import { useSmoothText } from "./useSmoothText";

/** Per-run wall-clock timing, keyed by run id (see ``useTaskAgentRun``). */
export type RunTimingMap = Record<
  string,
  { startedAtMs: number | null; endedAtMs: number | null }
>;

/** A user message or one agent run, used to bracket a turn with time + duration. */
interface Turn {
  key: string;
  kind: "user" | "agent";
  runId: string | null;
  blocks: Block[];
}

function blockRunId(b: Block): string | null {
  if (
    b.kind === "assistant" ||
    b.kind === "thinking" ||
    b.kind === "tool" ||
    b.kind === "plan"
  ) {
    return b.runId ?? null;
  }
  return null;
}

/**
 * Group the flat block list into turns: each user message is its own turn, and
 * a contiguous run of agent blocks (assistant / thinking / tool / subagent…)
 * collapses into one agent turn so we can show a single start time and a single
 * duration around it.
 */
function groupTurns(blocks: Block[]): Turn[] {
  const turns: Turn[] = [];
  for (const b of blocks) {
    if (b.kind === "user") {
      turns.push({ key: b.id, kind: "user", runId: null, blocks: [b] });
      continue;
    }
    const prev = turns[turns.length - 1];
    if (prev && prev.kind === "agent") {
      prev.blocks.push(b);
      if (prev.runId === null) prev.runId = blockRunId(b);
    } else {
      turns.push({ key: b.id, kind: "agent", runId: blockRunId(b), blocks: [b] });
    }
  }
  return turns;
}

export function Timeline({
  blocks,
  running,
  onOpenFile,
  agentName,
  runTiming,
}: {
  blocks: Block[];
  running: boolean;
  onOpenFile?: (path: string) => void;
  /** Display name shown above assistant messages (defaults to "deep-agent"). */
  agentName?: string;
  /** Per-run timing, used to show how long each agent turn took. */
  runTiming?: RunTimingMap;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [blocks, running]);

  const lastBlock = blocks[blocks.length - 1];
  // While streaming, show a caret on the in-progress assistant text and drop
  // the redundant "thinking…" spinner once tokens are arriving.
  const streamingText =
    running && lastBlock?.kind === "assistant" && lastBlock.text.trim().length > 0;

  const turns = useMemo(() => groupTurns(blocks), [blocks]);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6">
      {turns.map((turn) => {
        const startedAtMs = turn.blocks[0]?.createdAtMs;
        const timing = turn.runId ? runTiming?.[turn.runId] : undefined;
        const durationMs =
          timing?.startedAtMs && timing?.endedAtMs
            ? timing.endedAtMs - timing.startedAtMs
            : null;
        return (
          <div key={turn.key} className="flex flex-col gap-4">
            {startedAtMs ? (
              <TurnTimestamp ms={startedAtMs} align={turn.kind} />
            ) : null}
            {turn.blocks.map((b) => (
              <BlockView
                key={b.id}
                block={b}
                onOpenFile={onOpenFile}
                streaming={streamingText && b.id === lastBlock.id}
                agentName={agentName}
              />
            ))}
            {turn.kind === "agent" && durationMs != null ? (
              <TurnDuration ms={durationMs} />
            ) : null}
          </div>
        );
      })}
      {running && !streamingText && (
        <div className="flex items-center gap-2 pl-11 text-sm text-muted-foreground">
          <Spinner /> thinking…
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}

/** Subtle send-time shown at the start of a turn (right for user, left for agent). */
function TurnTimestamp({ ms, align }: { ms: number; align: "user" | "agent" }) {
  return (
    <div
      className={cn(
        "text-[11px] text-muted-foreground/80",
        align === "user" ? "text-right" : "pl-11",
      )}
    >
      {formatTimestamp(ms)}
    </div>
  );
}

/** "Completed in 1m 23s" footer at the end of an agent turn. */
function TurnDuration({ ms }: { ms: number }) {
  return (
    <div className="flex items-center gap-1 pl-11 text-[11px] text-muted-foreground/80">
      <Clock className="h-3 w-3 shrink-0" />
      <span>Completed in {formatDuration(ms)}</span>
    </div>
  );
}

/**
 * Pull `[file:<path>]` tokens (inserted when a user drags an Artifacts file
 * into the composer) out of a user message so we can render them as chips
 * instead of raw text. Returns the surviving prose with tokens stripped.
 */
function parseFileTokens(raw: string): { files: string[]; text: string } {
  const re = /\[file:([^\]]+)\]/g;
  const files: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(raw)) !== null) {
    const p = m[1].trim();
    if (p && !files.includes(p)) files.push(p);
  }
  const text = raw.replace(re, "").replace(/[ \t]+\n/g, "\n").trim();
  return { files, text };
}

function LinkedFiles({
  paths,
  onOpenFile,
}: {
  paths: string[];
  onOpenFile?: (path: string) => void;
}) {
  return (
    <div className="flex flex-wrap justify-end gap-1.5">
      {paths.map((p) => {
        const name = p.split("/").pop() || p;
        const clickable = !!onOpenFile;
        return (
          <button
            key={p}
            type="button"
            title={p}
            disabled={!clickable}
            onClick={clickable ? () => onOpenFile?.(p) : undefined}
            className={cn(
              "inline-flex max-w-[16rem] items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs font-medium text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300",
              clickable
                ? "cursor-pointer transition-colors hover:bg-amber-100 dark:hover:bg-amber-500/20"
                : "cursor-default",
            )}
          >
            <FileText className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{name}</span>
          </button>
        );
      })}
    </div>
  );
}

/** A small "name + avatar" label above a user bubble so a shared task thread
 * shows which collaborator sent each message. */
function SenderLabel({ sender }: { sender: Sender }) {
  const name = sender.name || (sender.type === "agent" ? "agent" : "user");
  return (
    <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
      <span className="max-w-[12rem] truncate">{name}</span>
      {sender.avatar ? (
        <img
          src={sender.avatar}
          alt={name}
          className="h-5 w-5 shrink-0 rounded-full object-cover"
        />
      ) : (
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-3 text-[10px] font-semibold uppercase text-muted-foreground">
          {name.slice(0, 1)}
        </span>
      )}
    </div>
  );
}

function UserAttachments({ attachments }: { attachments: UserAttachment[] }) {
  const images = attachments.filter((a) => a.kind === "image");
  const files = attachments.filter((a) => a.kind !== "image");
  return (
    <div className="flex flex-col items-end gap-2">
      {images.length > 0 && (
        <div className="flex flex-wrap justify-end gap-2">
          {images.map((a) =>
            a.url ? (
              <AuthedImage
                key={a.id}
                src={a.url}
                alt={a.name}
                className="max-h-48 max-w-[15rem] rounded-xl border border-border object-cover"
              />
            ) : (
              <span
                key={a.id}
                className="flex h-20 w-20 items-center justify-center rounded-xl border border-border bg-surface-2 text-muted-foreground"
              >
                <FileText className="h-5 w-5" />
              </span>
            ),
          )}
        </div>
      )}
      {files.map((a) => (
        <div
          key={a.id}
          className="flex items-center gap-2 rounded-xl border border-border bg-surface-2 px-3 py-2 text-sm text-foreground"
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <FileText className="h-4 w-4" />
          </span>
          <span className="max-w-[12rem] truncate font-medium">{a.name}</span>
        </div>
      ))}
    </div>
  );
}

function BlockView({
  block,
  onOpenFile,
  streaming,
  agentName,
}: {
  block: Block;
  onOpenFile?: (path: string) => void;
  streaming?: boolean;
  agentName?: string;
}) {
  switch (block.kind) {
    case "user": {
      const { files, text } = parseFileTokens(block.text ?? "");
      return (
        <div className="flex justify-end animate-fade-in">
          <div className="flex max-w-[80%] flex-col items-end gap-1.5">
            {block.sender && (block.sender.name || block.sender.avatar) && (
              <SenderLabel sender={block.sender} />
            )}
            {block.attachments && block.attachments.length > 0 && (
              <UserAttachments attachments={block.attachments} />
            )}
            {files.length > 0 && (
              <LinkedFiles paths={files} onOpenFile={onOpenFile} />
            )}
            {text && (
              <div className="whitespace-pre-wrap rounded-2xl rounded-br-md border border-border bg-surface-2 px-4 py-2.5 text-sm leading-relaxed text-foreground">
                {text}
              </div>
            )}
          </div>
        </div>
      );
    }
    case "assistant":
      return (
        <Row
          icon={<Sparkles className="h-4 w-4" />}
          role={agentName ?? "deep-agent"}
          accent
        >
          <AssistantText text={block.text} streaming={streaming} />
        </Row>
      );
    case "thinking":
      return <ThinkingView text={block.text} />;
    case "tool":
      if (block.name === TODO_TOOL) {
        return <PlanUpdateMarker />;
      }
      return (
        // Negative margin tightens consecutive tool rows against the
        // timeline's gap-4 so a burst of tool calls reads as one group.
        <div className="-my-1 pl-11">
          <ToolCard block={block} onOpenFile={onOpenFile} />
        </div>
      );
    case "plan":
      return <PlanChecklist block={block} />;
    case "subagent":
      return <SubagentView block={block} />;
    case "attachment":
      return <AttachmentView block={block} />;
    case "notice":
      return <NoticeView block={block} />;
    default:
      return null;
  }
}

function Row({
  icon,
  role,
  children,
  accent,
}: {
  icon: React.ReactNode;
  role: string;
  children: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="flex gap-3 animate-fade-in">
      <div
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-border",
          accent ? "bg-primary/15 text-primary" : "bg-card text-muted-foreground",
        )}
      >
        {icon}
      </div>
      <div className="min-w-0 flex-1 pt-1">
        <div className="mb-1 text-xs font-medium text-muted-foreground">{role}</div>
        {children}
      </div>
    </div>
  );
}

type AssistantSegment = { type: "text" | "thinking"; content: string };

/**
 * Split assistant text into normal segments and ``<thinking>``/``<think>``
 * segments. Handles a trailing *unclosed* tag (mid-stream) so reasoning is
 * collapsed the moment it starts streaming rather than flashing as raw text.
 */
function parseThinking(text: string): AssistantSegment[] {
  const segments: AssistantSegment[] = [];
  const pair = /<think(?:ing)?>([\s\S]*?)<\/think(?:ing)?>/gi;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = pair.exec(text)) !== null) {
    if (m.index > last) {
      segments.push({ type: "text", content: text.slice(last, m.index) });
    }
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

function StreamingCaret() {
  return (
    <span
      aria-hidden
      className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse rounded-full bg-primary align-baseline motion-reduce:animate-none"
    />
  );
}

function AssistantText({
  text,
  streaming,
}: {
  text: string;
  streaming?: boolean;
}) {
  // Reveal streamed text at a steady cadence so bursty tokens read as smooth
  // typing; non-streaming (historical/final) text is returned as-is.
  const display = useSmoothText(text, !!streaming);
  const segments = useMemo(() => parseThinking(display), [display]);
  const hasVisible = segments.some(
    (s) => s.type === "thinking" || s.content.trim(),
  );
  if (!hasVisible) return <Markdown>…</Markdown>;
  const lastTextIdx = segments.reduce(
    (acc, s, i) => (s.type === "text" && s.content.trim() ? i : acc),
    -1,
  );
  return (
    <div className="flex flex-col gap-2">
      {segments.map((seg, i) =>
        seg.type === "thinking" ? (
          <ThinkingDisclosure key={i} text={seg.content.trim()} />
        ) : seg.content.trim() ? (
          <div key={i}>
            <Markdown>{seg.content}</Markdown>
            {streaming && i === lastTextIdx && <StreamingCaret />}
          </div>
        ) : null,
      )}
    </div>
  );
}

/** Auto-collapsed reasoning disclosure (click to expand). */
function ThinkingDisclosure({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        <Brain className="h-3.5 w-3.5" />
        {open ? "Hide reasoning" : "Show reasoning"}
      </button>
      {open && (
        <div className="mt-1 whitespace-pre-wrap rounded-lg border border-dashed border-border bg-card/40 p-3 text-xs italic text-muted-foreground">
          {text}
        </div>
      )}
    </div>
  );
}

function ThinkingView({ text }: { text: string }) {
  return (
    <div className="pl-11">
      <ThinkingDisclosure text={text} />
    </div>
  );
}

function SubagentView({ block }: { block: import("./types").SubagentBlock }) {
  return (
    <div className="pl-11">
      <div className="flex items-center gap-2 rounded-lg border border-border bg-card/60 px-3 py-2 text-sm">
        <GitBranch className="h-3.5 w-3.5 text-violet-400" />
        <span className="font-medium">{block.agentType || "sub-agent"}</span>
        {block.description && (
          <span className="truncate text-xs text-muted-foreground">
            {block.description}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          {block.tokens ? (
            <span className="text-xs text-muted-foreground">
              {formatTokens(block.tokens)} tok
            </span>
          ) : null}
          <SubStatus status={block.status} />
        </span>
      </div>
      {block.error && (
        <div className="mt-1 text-xs text-destructive">{block.error}</div>
      )}
    </div>
  );
}

function SubStatus({
  status,
}: {
  status: import("./types").SubagentBlock["status"];
}) {
  if (status === "running") return <Spinner className="h-3.5 w-3.5 text-primary" />;
  if (status === "completed")
    return <Badge variant="success">done</Badge>;
  if (status === "killed") return <Badge variant="warning">killed</Badge>;
  return <Badge variant="destructive">failed</Badge>;
}

function AttachmentView({
  block,
}: {
  block: import("./types").AttachmentBlock;
}) {
  return (
    <div className="pl-11">
      <a
        href={block.url}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-3 rounded-lg border border-border bg-card/60 px-3 py-2 transition-colors hover:bg-accent"
      >
        <Download className="h-4 w-4 text-primary" />
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{block.filename}</div>
          {block.caption && (
            <div className="truncate text-xs text-muted-foreground">
              {block.caption}
            </div>
          )}
        </div>
        <span className="ml-auto text-xs text-muted-foreground">
          {formatBytes(block.size)}
        </span>
      </a>
    </div>
  );
}

/**
 * Live task checklist a CLI agent is working through. The agent re-sends its
 * whole list on each change, so this renders the current snapshot with a
 * done/total progress count and a per-row status icon.
 */
function PlanChecklist({ block }: { block: import("./types").PlanBlock }) {
  const entries = block.entries ?? [];
  if (entries.length === 0) return null;
  const done = entries.filter((e) => e.status === "done").length;
  return (
    <div className="pl-11">
      <div className="rounded-xl border border-border bg-card/60 p-3">
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <ListChecks className="h-3.5 w-3.5 text-primary" />
          <span>Plan</span>
          <span className="ml-auto tabular-nums">
            {done}/{entries.length}
          </span>
        </div>
        <ul className="flex flex-col gap-1.5">
          {entries.map((e, i) => (
            <li key={i} className="flex items-start gap-2 text-sm">
              <PlanStatusIcon status={e.status} />
              <span
                className={cn(
                  "leading-snug",
                  e.status === "done" && "text-muted-foreground line-through",
                  e.status === "in_progress" && "font-medium text-foreground",
                )}
              >
                {e.title}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function PlanStatusIcon({
  status,
}: {
  status: import("./types").PlanEntry["status"];
}) {
  if (status === "done") {
    return (
      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
    );
  }
  if (status === "in_progress") {
    return <Spinner className="mt-0.5 h-4 w-4 shrink-0 text-primary" />;
  }
  return <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/60" />;
}

function PlanUpdateMarker() {
  return (
    <div className="mx-auto flex items-center gap-2 rounded-full border border-border bg-card/60 px-3 py-1 text-xs text-muted-foreground">
      <ListChecks className="h-3.5 w-3.5 text-primary" />
      Plan updated
    </div>
  );
}

function NoticeView({ block }: { block: import("./types").NoticeBlock }) {
  const icon =
    block.variant === "error" ? (
      <AlertTriangle className="h-3.5 w-3.5 text-destructive" />
    ) : block.variant === "compaction" ? (
      <Scissors className="h-3.5 w-3.5" />
    ) : (
      <Info className="h-3.5 w-3.5" />
    );
  return (
    <div
      className={cn(
        "mx-auto flex items-center gap-2 rounded-full border px-3 py-1 text-xs",
        block.variant === "error"
          ? "border-destructive/40 bg-destructive/10 text-destructive"
          : "border-border bg-card/60 text-muted-foreground",
      )}
    >
      {icon}
      {block.text}
    </div>
  );
}
