import { Check } from "@/components/icons";
import { cn } from "@/lib/utils";

export type GoalStage = "plan" | "review" | "run" | "result";

const STEPS: { id: GoalStage; label: string; hint: string }[] = [
  { id: "plan", label: "Plan", hint: "Draft a contract" },
  { id: "review", label: "Review", hint: "Edit & approve" },
  { id: "run", label: "Run", hint: "Build & verify" },
  { id: "result", label: "Result", hint: "Verdict" },
];

/**
 * The four-stage progress rail for a goal. The whole goal lifecycle is one
 * linear flow — draft a plan, review/approve it, run it, read the verdict — so
 * the cockpit shows exactly one stage at a time with this rail for orientation.
 */
export function GoalStepper({
  current,
  terminal = false,
  onStepClick,
}: {
  current: GoalStage;
  /** A terminal Result is complete, not perpetually "In progress". */
  terminal?: boolean;
  /** Completed/current steps double as shortcuts into the goal package. */
  onStepClick?: (stage: GoalStage) => void;
}) {
  const currentIdx = STEPS.findIndex((s) => s.id === current);
  return (
    <ol className="flex items-center">
      {STEPS.map((step, i) => {
        const active = i === currentIdx;
        const done = i < currentIdx || (terminal && active);
        // The sub-label tracks live state so the rail doubles as a status line.
        const status = done ? "Completed" : active ? "In progress" : "Pending";
        const interactive = !!onStepClick && (done || active);
        return (
          <li key={step.id} className="flex flex-1 items-center gap-2 last:flex-none">
            <button
              type="button"
              disabled={!interactive}
              onClick={() => interactive && onStepClick?.(step.id)}
              className={cn(
                "flex min-w-0 items-center gap-2.5 rounded-md text-left",
                interactive && "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
              )}
            >
              <span
                className={cn(
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-[12px] font-semibold tabular-nums transition-colors",
                  done && !active &&
                    "border-emerald-500 bg-emerald-500 text-white",
                  active &&
                    (terminal
                      ? "border-emerald-500 bg-emerald-500 text-white shadow-[0_0_0_4px] shadow-emerald-500/15"
                      : "border-primary bg-primary text-primary-foreground shadow-[0_0_0_4px] shadow-primary/15"),
                  !done &&
                    !active &&
                    "border-border bg-surface-1 text-muted-foreground",
                )}
              >
                {done ? <Check className="h-4 w-4" /> : i + 1}
              </span>
              <div className="min-w-0 leading-tight">
                <p
                  className={cn(
                    "truncate text-[13px] font-semibold",
                    active
                      ? "text-foreground"
                      : done
                        ? "text-foreground/80"
                        : "text-muted-foreground",
                  )}
                >
                  {step.label}
                </p>
                <p
                  className={cn(
                    "truncate text-[11px]",
                    active && !terminal
                      ? "text-primary dark:text-primary"
                      : done
                        ? "text-emerald-600 dark:text-emerald-400"
                        : "text-muted-foreground/70",
                  )}
                >
                  {status}
                </p>
              </div>
            </button>
            {i < STEPS.length - 1 && (
              <span className="h-0.5 flex-1 overflow-hidden rounded-full bg-border">
                <span
                  className={cn(
                    "block h-full rounded-full transition-all",
                    done ? "w-full bg-emerald-500" : active ? "w-1/2 bg-primary" : "w-0",
                  )}
                />
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
