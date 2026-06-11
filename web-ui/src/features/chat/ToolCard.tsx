import {
  CheckCircle2,
  ChevronRight,
  FileText,
  XCircle,
} from "@/components/icons";
import { useState } from "react";
import { CodeView } from "@/components/CodeView";
import { DiffStatBadge, DiffView, diffStats } from "@/components/DiffView";
import { Spinner } from "@/components/ui/spinner";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ToolBlock } from "./types";

const WRITE_TOOLS = new Set(["write_file", "write"]);
const EDIT_TOOLS = new Set(["edit", "edit_file"]);

export function ToolCard({
  block,
  onOpenFile,
}: {
  block: ToolBlock;
  onOpenFile?: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const arg = summarizeInput(block.input);
  const filePath = pickPath(block.input);
  const isWrite = WRITE_TOOLS.has(block.name);
  const isEdit = EDIT_TOOLS.has(block.name);

  const editStats =
    isEdit &&
    typeof block.input.old_string === "string" &&
    typeof block.input.new_string === "string"
      ? diffStats(block.input.old_string, block.input.new_string)
      : null;

  const fileName = filePath ? filePath.split("/").pop() : "";
  // Compact one-liner (Jira/Linear style): slim row, expands on click.
  return (
    <div
      className={cn(
        "rounded border text-left transition-colors",
        open
          ? "border-border bg-surface-1/70"
          : "border-transparent hover:border-border hover:bg-surface-1/70",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-1.5 py-1 text-left"
      >
        <ChevronRight
          className={cn(
            "h-3 w-3 shrink-0 text-muted-foreground/70 transition-transform",
            open && "rotate-90",
          )}
        />
        <StatusIcon status={block.status} />
        <span className="shrink-0 font-mono text-[12px] font-medium text-foreground">
          {block.name}
        </span>
        {(isWrite || isEdit) && fileName ? (
          <span className="truncate rounded-sm bg-surface-3/70 px-1 py-px font-mono text-[11px] text-muted-foreground">
            {fileName}
          </span>
        ) : (
          arg && (
            <span className="truncate font-mono text-[11px] text-muted-foreground/80">
              {arg}
            </span>
          )
        )}
        {editStats && (
          <span className="ml-auto shrink-0">
            <DiffStatBadge {...editStats} />
          </span>
        )}
        <span
          className={cn(
            "shrink-0 font-mono text-[10.5px] text-muted-foreground/70 tabular",
            !editStats && "ml-auto",
          )}
        >
          {block.durationMs != null ? formatDuration(block.durationMs) : null}
        </span>
      </button>
      {open && (
        <div className="border-t border-border px-2 py-2">
          {filePath && onOpenFile && (
            <button
              type="button"
              onClick={() => onOpenFile(filePath)}
              className="mb-2 flex items-center gap-1.5 rounded border border-border bg-surface-2 px-2 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
            >
              <FileText className="h-3.5 w-3.5" />
              Open current file
            </button>
          )}
          {isWrite ? (
            <WritePreview input={block.input} />
          ) : isEdit ? (
            <EditPreview input={block.input} />
          ) : (
            Object.keys(block.input).length > 0 && (
              <pre className="mb-2 max-h-48 overflow-auto rounded bg-[hsl(var(--code-bg))] p-2 text-[11px] leading-relaxed text-[hsl(210_14%_88%)] scrollbar-thin">
                {JSON.stringify(block.input, null, 2)}
              </pre>
            )
          )}
          {(block.progress || block.outputPreview) && (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-[hsl(var(--code-bg))] p-2 text-[11px] leading-relaxed text-[hsl(210_14%_88%)] scrollbar-thin">
              {block.outputPreview ?? block.progress}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function WritePreview({ input }: { input: Record<string, unknown> }) {
  const path = pickPath(input);
  const content = typeof input.content === "string" ? input.content : "";
  return (
    <div className="mb-2">
      {path && (
        <div className="mb-1 font-mono text-xs text-muted-foreground">{path}</div>
      )}
      <div className="max-h-72 overflow-auto rounded scrollbar-thin">
        <CodeView content={content} path={path} />
      </div>
    </div>
  );
}

function EditPreview({ input }: { input: Record<string, unknown> }) {
  const path = pickPath(input);
  const oldStr = typeof input.old_string === "string" ? input.old_string : "";
  const newStr = typeof input.new_string === "string" ? input.new_string : "";
  return (
    <div className="mb-2">
      {path && (
        <div className="mb-1 font-mono text-xs text-muted-foreground">{path}</div>
      )}
      <DiffView oldText={oldStr} newText={newStr} maxHeightClass="max-h-72" />
    </div>
  );
}

function StatusIcon({ status }: { status: ToolBlock["status"] }) {
  if (status === "running") return <Spinner className="h-3.5 w-3.5 text-primary" />;
  if (status === "error")
    return <XCircle className="h-3.5 w-3.5 text-destructive" />;
  return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />;
}

function pickPath(input: Record<string, unknown>): string {
  for (const k of ["path", "file_path"]) {
    const v = input[k];
    if (typeof v === "string" && v) return v;
  }
  return "";
}

function summarizeInput(input: Record<string, unknown>): string {
  const keys = ["command", "path", "file_path", "query", "url", "pattern", "skill"];
  for (const k of keys) {
    const v = input[k];
    if (typeof v === "string" && v) return v.length > 80 ? `${v.slice(0, 80)}…` : v;
  }
  return "";
}
