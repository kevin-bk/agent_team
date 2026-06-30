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
export function GoalStepper({ current }: { current: GoalStage }) {
  const currentIdx = STEPS.findIndex((s) => s.id === current);
  return (
    <ol className="flex items-center">
      {STEPS.map((step, i) => {
        const done = i < currentIdx;
        const active = i === currentIdx;
        // The sub-label tracks live state so the rail doubles as a status line.
        const status = done ? "Completed" : active ? "In progress" : "Pending";
        return (
          <li key={step.id} className="flex flex-1 items-center gap-2 last:flex-none">
            <div className="flex min-w-0 items-center gap-2.5">
              <span
                className={cn(
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-[12px] font-semibold tabular-nums transition-colors",
                  done &&
                    "border-emerald-500 bg-emerald-500 text-white",
                  active &&
                    "border-primary bg-primary text-primary-foreground shadow-[0_0_0_4px] shadow-primary/15",
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
                    active
                      ? "text-primary dark:text-primary"
                      : done
                        ? "text-emerald-600 dark:text-emerald-400"
                        : "text-muted-foreground/70",
                  )}
                >
                  {status}
                </p>
              </div>
            </div>
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
