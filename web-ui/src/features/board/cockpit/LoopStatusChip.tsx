import type { LoopState } from "@/api/types";
import { cn } from "@/lib/utils";
import { LOOP_STATE_META } from "./loopStatus";

/**
 * Compact badge for a task's autonomous-loop state. Shows the live attempt
 * counter while running. Renders nothing when the task has no loop state.
 */
export function LoopStatusChip({
  state,
  attempt,
  maxAttempts,
  className,
}: {
  state?: LoopState | null;
  attempt?: number;
  maxAttempts?: number;
  className?: string;
}) {
  if (!state) return null;
  const meta = LOOP_STATE_META[state];
  if (!meta) return null;
  const Icon = meta.icon;
  const showCount =
    meta.active && typeof attempt === "number" && attempt > 0;
  return (
    <span
      title={`Autonomous loop: ${meta.label}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.03em]",
        meta.tone,
        className,
      )}
    >
      <Icon className={cn("h-3 w-3", meta.active && "animate-spin")} />
      {meta.label}
      {showCount && (
        <span className="font-mono tabular-nums opacity-80">
          {attempt}
          {maxAttempts ? `/${maxAttempts}` : ""}
        </span>
      )}
    </span>
  );
}
