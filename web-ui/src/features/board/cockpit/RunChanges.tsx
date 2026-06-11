import { ChevronRight, FileText, Pencil, Plus } from "@/components/icons";
import { useMemo, useState } from "react";
import { CodeView } from "@/components/CodeView";
import { DiffStatBadge, DiffView, diffStats } from "@/components/DiffView";
import { cn } from "@/lib/utils";
import type { Block } from "@/features/chat/types";

// Mirror the tool names ToolCard treats as file mutations.
const WRITE_TOOLS = new Set(["write_file", "write"]);
const EDIT_TOOLS = new Set(["edit", "edit_file"]);

interface FileOp {
  kind: "write" | "edit";
  oldText: string;
  newText: string;
  added: number;
  removed: number;
}

interface FileChange {
  path: string;
  added: number;
  removed: number;
  ops: FileOp[];
}

function pickPath(input: Record<string, unknown>): string {
  for (const k of ["path", "file_path"]) {
    const v = input[k];
    if (typeof v === "string" && v) return v;
  }
  return "";
}

/** Collapse every write/edit tool call in a thread into a per-file changeset. */
function changesFromBlocks(blocks: Block[]): FileChange[] {
  const byPath = new Map<string, FileChange>();
  for (const b of blocks) {
    if (b.kind !== "tool") continue;
    const isWrite = WRITE_TOOLS.has(b.name);
    const isEdit = EDIT_TOOLS.has(b.name);
    if (!isWrite && !isEdit) continue;
    const path = pickPath(b.input);
    if (!path) continue;

    let op: FileOp;
    if (isWrite) {
      const content = typeof b.input.content === "string" ? b.input.content : "";
      const stats = diffStats("", content);
      op = { kind: "write", oldText: "", newText: content, ...stats };
    } else {
      const oldText =
        typeof b.input.old_string === "string" ? b.input.old_string : "";
      const newText =
        typeof b.input.new_string === "string" ? b.input.new_string : "";
      op = { kind: "edit", oldText, newText, ...diffStats(oldText, newText) };
    }

    const existing = byPath.get(path);
    if (existing) {
      existing.ops.push(op);
      existing.added += op.added;
      existing.removed += op.removed;
    } else {
      byPath.set(path, {
        path,
        added: op.added,
        removed: op.removed,
        ops: [op],
      });
    }
  }
  return [...byPath.values()];
}

/** Number of distinct files touched — for the toolbar toggle badge. */
export function changedFileCount(blocks: Block[]): number {
  return changesFromBlocks(blocks).length;
}

/**
 * PR-style summary of every file the agent touched in this thread. Built
 * entirely from the streamed write/edit tool calls — no server round-trip.
 */
export function RunChanges({
  blocks,
  onOpenFile,
}: {
  blocks: Block[];
  onOpenFile?: (path: string) => void;
}) {
  const changes = useMemo(() => changesFromBlocks(blocks), [blocks]);

  if (changes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-center text-sm text-slate-400">
        No file changes yet — the agent hasn't written or edited any files in
        this thread.
      </div>
    );
  }

  const totalAdded = changes.reduce((n, c) => n + c.added, 0);
  const totalRemoved = changes.reduce((n, c) => n + c.removed, 0);

  return (
    <div className="min-h-0 flex-1 overflow-auto px-3 py-3 scrollbar-thin">
      <div className="mx-auto max-w-3xl">
        <div className="mb-2 flex items-center gap-2 text-xs text-slate-500 dark:text-muted-foreground">
          <span className="font-semibold">
            {changes.length} file{changes.length === 1 ? "" : "s"} changed
          </span>
          <DiffStatBadge added={totalAdded} removed={totalRemoved} />
        </div>
        <div className="space-y-2">
          {changes.map((c) => (
            <FileChangeRow key={c.path} change={c} onOpenFile={onOpenFile} />
          ))}
        </div>
      </div>
    </div>
  );
}

function FileChangeRow({
  change,
  onOpenFile,
}: {
  change: FileChange;
  onOpenFile?: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const fileName = change.path.split("/").pop() || change.path;
  return (
    <div className="rounded-lg border border-border bg-surface-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate font-mono text-[13px]" title={change.path}>
          {fileName}
        </span>
        <span className="ml-auto shrink-0">
          <DiffStatBadge added={change.added} removed={change.removed} />
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border px-3 py-2">
          <div className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
            <span className="truncate" title={change.path}>
              {change.path}
            </span>
            {onOpenFile && (
              <button
                type="button"
                onClick={() => onOpenFile(change.path)}
                className="ml-auto shrink-0 rounded border border-border bg-surface-2 px-2 py-0.5 transition-colors hover:border-primary/40 hover:text-foreground"
              >
                Open current
              </button>
            )}
          </div>
          {change.ops.map((op, i) => (
            <div key={`${op.kind}-${i}`}>
              <div className="mb-1 flex items-center gap-1.5 text-[10.5px] uppercase tracking-wide text-muted-foreground">
                {op.kind === "write" ? (
                  <Plus className="h-3 w-3" />
                ) : (
                  <Pencil className="h-3 w-3" />
                )}
                {op.kind === "write" ? "wrote file" : "edited"}
              </div>
              {op.kind === "write" ? (
                <div className="max-h-72 overflow-auto rounded scrollbar-thin">
                  <CodeView content={op.newText} path={change.path} />
                </div>
              ) : (
                <DiffView oldText={op.oldText} newText={op.newText} />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
