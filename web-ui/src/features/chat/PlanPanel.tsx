import {
  CheckCircle2,
  ChevronDown,
  Circle,
  ListChecks,
  Loader2,
  XCircle,
} from "@/components/icons";
import { useState } from "react";
import { cn } from "@/lib/utils";
import type { Plan, TodoStatus } from "./plan";

export function PlanPanel({ plan }: { plan: Plan }) {
  const [open, setOpen] = useState(true);
  const { todos, summary } = plan;
  const done = summary.completed;
  const active = todos.length - summary.completed - summary.cancelled;
  const pct = summary.total ? Math.round((done / summary.total) * 100) : 0;

  return (
    <div className="border-b border-border bg-card/40">
      <div className="mx-auto w-full max-w-3xl px-4">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2 py-2.5 text-left"
        >
          <ListChecks className="h-4 w-4 shrink-0 text-primary" />
          <span className="text-sm font-medium">Plan</span>
          <span className="text-xs text-muted-foreground">
            {done}/{summary.total} done
            {active > 0 ? ` · ${active} active` : ""}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <div className="hidden h-1.5 w-24 overflow-hidden rounded-full bg-border sm:block">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
            <ChevronDown
              className={cn(
                "h-4 w-4 text-muted-foreground transition-transform",
                !open && "-rotate-90",
              )}
            />
          </div>
        </button>
        {open && (
          <ul className="max-h-56 space-y-1 overflow-y-auto pb-3 scrollbar-thin">
            {todos.map((t) => (
              <li key={t.id} className="flex items-start gap-2 text-sm">
                <StatusIcon status={t.status} />
                <span
                  className={cn(
                    "leading-relaxed",
                    t.status === "completed" &&
                      "text-muted-foreground line-through",
                    t.status === "cancelled" &&
                      "text-muted-foreground/60 line-through",
                    t.status === "in_progress" && "font-medium text-foreground",
                  )}
                >
                  {t.content}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function StatusIcon({ status }: { status: TodoStatus }) {
  const base = "mt-0.5 h-3.5 w-3.5 shrink-0";
  if (status === "in_progress")
    return <Loader2 className={cn(base, "animate-spin text-primary")} />;
  if (status === "completed")
    return <CheckCircle2 className={cn(base, "text-emerald-400")} />;
  if (status === "cancelled")
    return <XCircle className={cn(base, "text-muted-foreground/60")} />;
  return <Circle className={cn(base, "text-muted-foreground")} />;
}
