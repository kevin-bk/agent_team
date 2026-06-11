import { MessageSquarePlus, MessagesSquare } from "@/components/icons";
import { useState } from "react";
import { useApi } from "@/api/ApiProvider";
import { useConversation } from "@/api/hooks";
import { formatCost, formatTokens } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Composer } from "./Composer";
import { FileViewer } from "./FileViewer";
import { PlanPanel } from "./PlanPanel";
import { Timeline } from "./Timeline";
import type { ContextSnapshot } from "./types";
import { useConversationRun } from "./useConversationRun";

export function ChatView({ convId }: { convId: string | undefined }) {
  const { client } = useApi();
  const { data: detail } = useConversation(convId);
  const { blocks, running, usage, context, plan, send, steer, cancel } =
    useConversationRun(convId);
  const [openFile, setOpenFile] = useState<string | null>(null);

  if (!convId) return <EmptyState />;

  const conv = detail?.conversation;
  // Live run telemetry wins; fall back to the persisted idle estimate so
  // the meter is visible even before sending a message.
  const ctx = context ?? detail?.context ?? null;
  const spentUsd = usage?.spentUsd ?? conv?.total_cost_usd ?? 0;
  const spentTokens = usage?.spentTokens ?? conv?.total_tokens ?? 0;
  // Prefer the live run snapshot; fall back to persisted conversation
  // totals so the breakdown is correct after a reload too.
  const tokenBreakdown = {
    input: usage?.inputTokens ?? conv?.total_input_tokens ?? 0,
    output: usage?.outputTokens ?? conv?.total_output_tokens ?? 0,
    cacheRead: usage?.cacheReadTokens ?? conv?.total_cache_read_tokens ?? 0,
    cacheWrite: usage?.cacheCreationTokens ?? conv?.total_cache_creation_tokens ?? 0,
  };
  const cacheTokens = tokenBreakdown.cacheRead + tokenBreakdown.cacheWrite;
  const tokenTitle = [
    `Input: ${tokenBreakdown.input.toLocaleString()}`,
    `Output: ${tokenBreakdown.output.toLocaleString()}`,
    `Cache read: ${tokenBreakdown.cacheRead.toLocaleString()}`,
    `Cache write: ${tokenBreakdown.cacheWrite.toLocaleString()}`,
    `Total: ${spentTokens.toLocaleString()}`,
  ].join("\n");

  return (
    <div className="flex h-full min-h-0">
      <div className="flex h-full min-h-0 flex-1 flex-col">
      <header className="flex items-center gap-3 border-b border-border bg-card px-6 py-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sky-100 text-sky-600 dark:bg-sky-500/15 dark:text-sky-300">
          <MessagesSquare className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <h1 className="truncate text-[15px] font-semibold tracking-tight">
            {conv?.title || "Untitled conversation"}
          </h1>
          <p className="truncate text-xs text-muted-foreground">
            {conv?.profile_name}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-3">
          {ctx && <ContextMeter context={ctx} />}
          <div
            className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1 font-mono text-[11px] text-muted-foreground tabular"
            title={tokenTitle}
          >
            <span>
              <span className="opacity-60">in</span> {formatTokens(tokenBreakdown.input)}
            </span>
            <span className="text-border-strong">·</span>
            <span>
              <span className="opacity-60">out</span> {formatTokens(tokenBreakdown.output)}
            </span>
            <span className="text-border-strong">·</span>
            <span>
              <span className="opacity-60">cache</span> {formatTokens(cacheTokens)}
            </span>
          </div>
          <span className="font-mono text-[11px] text-muted-foreground tabular">
            {formatCost(spentUsd)}
          </span>
        </div>
      </header>

      {plan && <PlanPanel plan={plan} />}

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
        {blocks.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Send a message to start.
          </div>
        ) : (
          <Timeline
            blocks={blocks}
            running={running}
            onOpenFile={setOpenFile}
          />
        )}
      </div>

      <Composer
        running={running}
        onSend={send}
        onSteer={steer}
        onCancel={cancel}
        onUpload={
          convId
            ? (files) => client.uploadConversationAttachments(convId, files)
            : undefined
        }
        onDeleteUpload={
          convId
            ? (dto) => client.deleteConversationAttachment(convId, dto.id)
            : undefined
        }
      />
      </div>
      {openFile !== null && (
        <FileViewer
          key={openFile}
          convId={convId}
          initialPath={openFile}
          onClose={() => setOpenFile(null)}
        />
      )}
    </div>
  );
}

/**
 * Live context-window fill meter. The denominator is the *summary
 * threshold* (where auto-compaction fires), so the bar filling up is a
 * direct "how close are we to a summary" signal. Turns amber past 75 %
 * and red past 90 %.
 */
function ContextMeter({ context }: { context: ContextSnapshot }) {
  const { tokens, limit, window } = context;
  if (!limit || tokens <= 0) return null;
  const pct = Math.min(tokens / limit, 1);
  const near = pct >= 0.75;
  const critical = pct >= 0.9;
  const title = [
    `Context: ${tokens.toLocaleString()} tokens`,
    `Summary threshold: ${limit.toLocaleString()} tokens (${Math.round(
      pct * 100,
    )}% full)`,
    window ? `Model window: ${window.toLocaleString()} tokens` : "",
    "Auto-summarises the history when the bar fills.",
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <div
      className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground tabular"
      title={title}
    >
      <span className="hidden sm:inline opacity-60">ctx</span>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-3">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            critical
              ? "bg-destructive"
              : near
                ? "bg-amber-500"
                : "bg-primary",
          )}
          style={{ width: `${Math.max(pct * 100, 3)}%` }}
        />
      </div>
      <span className={cn(critical && "text-destructive", near && !critical && "text-amber-500")}>
        {formatTokens(tokens)}/{formatTokens(limit)}
      </span>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center text-muted-foreground">
      <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-sky-100 text-sky-500 dark:bg-sky-500/15 dark:text-sky-300">
        <MessageSquarePlus className="h-8 w-8" />
      </span>
      <div>
        <p className="text-sm font-medium text-foreground">No conversation selected</p>
        <p className="text-sm">Pick a session or start a new one.</p>
      </div>
    </div>
  );
}
