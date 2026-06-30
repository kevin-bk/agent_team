import { ChevronRight, ExternalLink } from "@/components/icons";
import { useState } from "react";
import { useTaskChangeDiff } from "@/api/hooks";
import type { TaskChangeEntry } from "@/api/types";
import { DiffStatBadge } from "@/components/DiffView";
import { Markdown } from "@/components/Markdown";
import { TaskDiff, type DiffMode } from "@/components/TaskDiff";
import { Spinner } from "@/components/ui/spinner";
import { isMarkdownPath } from "@/lib/monacoLanguage";
import { cn } from "@/lib/utils";
import { StatusBadge } from "./changeMeta";

const MODES: { mode: DiffMode; label: string }[] = [
  { mode: "old", label: "Old" },
  { mode: "diff", label: "Diff" },
  { mode: "new", label: "New" },
];

/**
 * One collapsible file in the Changes view. Collapsed by default; expanding
 * lazily fetches the file's old/new content and renders it through {@link
 * TaskDiff} (Monaco) with a 3-way old/diff/new toggle. Markdown renders as a
 * preview in old/new mode. Binary/deleted files get a clear placeholder.
 */
export function FileDiffCard({
  taskId,
  entry,
  showRepo = false,
  defaultOpen = false,
  onOpenFile,
}: {
  taskId: string;
  entry: TaskChangeEntry;
  /** Prefix the path with the repo slug (when the task has >1 repo). */
  showRepo?: boolean;
  defaultOpen?: boolean;
  /** Open the current working file (e.g. as a tab); omitted ⇒ no button. */
  onOpenFile?: (repo: string, path: string) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [mode, setMode] = useState<DiffMode>("diff");
  const diff = useTaskChangeDiff({
    taskId,
    repo: entry.repo,
    path: entry.path,
    enabled: open && !entry.binary,
  });

  const isDeleted = entry.status === "D";
  const displayPath = showRepo ? `${entry.repo}/${entry.path}` : entry.path;
  const markdown = isMarkdownPath(entry.path);

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        <StatusBadge status={entry.status} />
        <span
          className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground"
          title={
            entry.old_path
              ? `${entry.old_path} → ${displayPath}`
              : displayPath
          }
        >
          {entry.old_path && (
            <span className="text-muted-foreground">
              {entry.old_path.split("/").pop()} →{" "}
            </span>
          )}
          {displayPath}
        </span>
        <span className="ml-auto shrink-0">
          {entry.binary ? (
            <span className="font-mono text-[11px] text-muted-foreground">
              binary
            </span>
          ) : (
            <DiffStatBadge added={entry.additions} removed={entry.deletions} />
          )}
        </span>
      </button>

      {open && (
        <div className="border-t border-border">
          {/* per-file action row: 3-way toggle + open */}
          <div className="flex items-center gap-2 px-3 py-1.5">
            {!entry.binary && (
              <div className="inline-flex rounded-md border border-border bg-surface-2 p-0.5">
                {MODES.map(({ mode: m, label }) => {
                  const disabled =
                    (m === "new" && isDeleted) ||
                    (m === "old" && entry.status === "A");
                  return (
                    <button
                      key={m}
                      type="button"
                      disabled={disabled}
                      onClick={() => setMode(m)}
                      className={cn(
                        "rounded px-2 py-0.5 text-[11px] font-medium transition-colors disabled:opacity-30",
                        mode === m
                          ? "bg-primary/10 text-primary"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            )}
            {onOpenFile && !isDeleted && (
              <button
                type="button"
                onClick={() => onOpenFile(entry.repo, entry.path)}
                title="Open the current file"
                className="ml-auto inline-flex items-center gap-1 rounded border border-border bg-surface-2 px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
              >
                <ExternalLink className="h-3 w-3" /> Open
              </button>
            )}
          </div>

          <div className="min-w-0">
            {entry.binary ? (
              <Placeholder text="Binary file — not shown as text." />
            ) : diff.isLoading ? (
              <div className="flex items-center gap-2 px-3 py-4 text-sm text-muted-foreground">
                <Spinner className="h-4 w-4" /> loading diff…
              </div>
            ) : diff.isError ? (
              <Placeholder text="Failed to load this diff." tone="error" />
            ) : diff.data ? (
              diff.data.binary ? (
                <Placeholder text="Binary file — not shown as text." />
              ) : markdown && mode !== "diff" ? (
                <div className="prose-chat mx-auto max-w-3xl px-5 py-4">
                  <Markdown>
                    {mode === "old" ? diff.data.original : diff.data.modified}
                  </Markdown>
                </div>
              ) : (
                <TaskDiff
                  original={diff.data.original}
                  modified={diff.data.modified}
                  path={entry.path}
                  mode={mode}
                />
              )
            ) : null}
            {diff.data?.truncated && (
              <p className="px-3 py-1.5 text-[11px] text-amber-600 dark:text-amber-400">
                Large file — diff was truncated.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Placeholder({
  text,
  tone = "muted",
}: {
  text: string;
  tone?: "muted" | "error";
}) {
  return (
    <p
      className={cn(
        "px-3 py-6 text-center text-sm",
        tone === "error" ? "text-rose-500" : "text-muted-foreground",
      )}
    >
      {text}
    </p>
  );
}
