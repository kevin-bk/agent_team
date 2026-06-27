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
    <ol className="flex items-center gap-1">
      {STEPS.map((step, i) => {
        const done = i < currentIdx;
        const active = i === currentIdx;
        return (
          <li key={step.id} className="flex flex-1 items-center gap-1">
            <div className="flex min-w-0 items-center gap-2">
              <span
                className={cn(
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold tabular-nums transition-colors",
                  done &&
                    "border-emerald-500 bg-emerald-500 text-white dark:border-emerald-500",
                  active &&
                    "border-primary bg-primary text-primary-foreground shadow-sm",
                  !done &&
                    !active &&
                    "border-border bg-surface-1 text-muted-foreground",
                )}
              >
                {done ? <Check className="h-3.5 w-3.5" /> : i + 1}
              </span>
              <div className="min-w-0 leading-tight">
                <p
                  className={cn(
                    "truncate text-[12.5px] font-semibold",
                    active
                      ? "text-foreground"
                      : done
                        ? "text-foreground/70"
                        : "text-muted-foreground",
                  )}
                >
                  {step.label}
                </p>
                <p className="truncate text-[10.5px] text-muted-foreground">
                  {step.hint}
                </p>
              </div>
            </div>
            {i < STEPS.length - 1 && (
              <span
                className={cn(
                  "h-px flex-1 transition-colors",
                  i < currentIdx ? "bg-emerald-500/60" : "bg-border",
                )}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
