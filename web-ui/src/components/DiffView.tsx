import { diffLines } from "diff";
import { useMemo } from "react";
import { cn } from "@/lib/utils";

export interface DiffStats {
  added: number;
  removed: number;
}

interface DiffRow {
  type: "add" | "del" | "ctx";
  oldNo: number | null;
  newNo: number | null;
  text: string;
}

/** Split a jsdiff value into rendered rows, dropping the trailing empty line. */
function rowsFromValue(value: string): string[] {
  const lines = value.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/** Build line-numbered diff rows from the old/new text pair. */
function buildRows(oldText: string, newText: string): DiffRow[] {
  const parts = diffLines(oldText, newText);
  const rows: DiffRow[] = [];
  let oldNo = 1;
  let newNo = 1;
  for (const part of parts) {
    const lines = rowsFromValue(part.value);
    for (const text of lines) {
      if (part.added) {
        rows.push({ type: "add", oldNo: null, newNo: newNo++, text });
      } else if (part.removed) {
        rows.push({ type: "del", oldNo: oldNo++, newNo: null, text });
      } else {
        rows.push({ type: "ctx", oldNo: oldNo++, newNo: newNo++, text });
      }
    }
  }
  return rows;
}

/** Count added / removed lines for a summary badge (e.g. "+12 −3"). */
export function diffStats(oldText: string, newText: string): DiffStats {
  let added = 0;
  let removed = 0;
  for (const part of diffLines(oldText, newText)) {
    if (!part.added && !part.removed) continue;
    const count = part.count ?? rowsFromValue(part.value).length;
    if (part.added) added += count;
    else if (part.removed) removed += count;
  }
  return { added, removed };
}

/** Compact "+a −b" badge; greys out when there are no changes. */
export function DiffStatBadge({ added, removed }: DiffStats) {
  if (added === 0 && removed === 0) {
    return <span className="font-mono text-[11px] text-muted-foreground">no change</span>;
  }
  return (
    <span className="font-mono text-[11px] tabular">
      {added > 0 && <span className="text-emerald-400">+{added}</span>}
      {added > 0 && removed > 0 && " "}
      {removed > 0 && <span className="text-rose-400">−{removed}</span>}
    </span>
  );
}

const ROW_STYLES: Record<DiffRow["type"], string> = {
  add: "bg-emerald-500/10 text-emerald-200",
  del: "bg-rose-500/10 text-rose-200",
  ctx: "text-muted-foreground",
};

const SIGN: Record<DiffRow["type"], string> = { add: "+", del: "−", ctx: " " };

/**
 * Unified, line-numbered diff between two text blobs. Built on jsdiff's
 * line differ and styled to match the cockpit's code surfaces. Used both
 * inline in tool cards (edit previews) and in the per-run Changes view.
 */
export function DiffView({
  oldText,
  newText,
  className,
  maxHeightClass = "max-h-80",
}: {
  oldText: string;
  newText: string;
  className?: string;
  maxHeightClass?: string;
}) {
  const rows = useMemo(() => buildRows(oldText, newText), [oldText, newText]);
  return (
    <div
      className={cn(
        "overflow-auto rounded bg-[hsl(var(--code-bg))] font-mono text-[11px] leading-relaxed scrollbar-thin",
        maxHeightClass,
        className,
      )}
    >
      <table className="w-full border-collapse">
        <tbody>
          {rows.map((row, i) => (
            <tr key={`${row.type}-${i}`} className={ROW_STYLES[row.type]}>
              <td className="select-none border-r border-border/40 px-2 text-right text-muted-foreground/60 tabular">
                {row.oldNo ?? ""}
              </td>
              <td className="select-none border-r border-border/40 px-2 text-right text-muted-foreground/60 tabular">
                {row.newNo ?? ""}
              </td>
              <td className="select-none px-1 text-center">{SIGN[row.type]}</td>
              <td className="whitespace-pre-wrap break-all px-2">{row.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
