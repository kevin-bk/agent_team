import type { GitChangeStatus } from "@/api/types";
import { cn } from "@/lib/utils";

/** Label + tinted chip styling per git change status (A/M/D/R/U). */
export const STATUS_META: Record<
  GitChangeStatus,
  { label: string; letter: string; cls: string; dot: string }
> = {
  A: {
    label: "Added",
    letter: "A",
    cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
    dot: "bg-emerald-500",
  },
  M: {
    label: "Modified",
    letter: "M",
    cls: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
    dot: "bg-amber-500",
  },
  D: {
    label: "Deleted",
    letter: "D",
    cls: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
    dot: "bg-rose-500",
  },
  R: {
    label: "Renamed",
    letter: "R",
    cls: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
    dot: "bg-violet-500",
  },
  U: {
    label: "Untracked",
    letter: "U",
    cls: "bg-teal-100 text-teal-700 dark:bg-teal-500/15 dark:text-teal-300",
    dot: "bg-teal-500",
  },
};

/** A small square letter chip (A/M/D/R/U) used in change rows and the tree. */
export function StatusBadge({
  status,
  className,
}: {
  status: GitChangeStatus;
  className?: string;
}) {
  const m = STATUS_META[status];
  return (
    <span
      title={m.label}
      className={cn(
        "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-[11px] font-bold",
        m.cls,
        className,
      )}
    >
      {m.letter}
    </span>
  );
}
