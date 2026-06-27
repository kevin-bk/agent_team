import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Loader2,
  Pencil,
  XCircle,
} from "@/components/icons";
import type { LoopState, LoopVerdict } from "@/api/types";

interface StateMeta {
  label: string;
  /** Tailwind classes for a soft tinted badge. */
  tone: string;
  icon: typeof CheckCircle2;
  /** Whether the loop is actively progressing (animated spinner). */
  active?: boolean;
}

/** Visuals for each persisted loop state, reused by the chip and panel. */
export const LOOP_STATE_META: Record<LoopState, StateMeta> = {
  planning: {
    label: "Planning",
    tone: "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300",
    icon: Loader2,
    active: true,
  },
  waiting_plan_approval: {
    label: "Plan ready for review",
    tone: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
    icon: Pencil,
  },
  plan_approved: {
    label: "Plan approved",
    tone: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
    icon: CheckCircle2,
  },
  plan_change_requested: {
    label: "Plan change requested",
    tone: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
    icon: AlertTriangle,
  },
  running: {
    label: "Running",
    tone: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
    icon: Loader2,
    active: true,
  },
  complete: {
    label: "Complete",
    tone: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
    icon: CheckCircle2,
  },
  waiting_for_human: {
    label: "Needs review",
    tone: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
    icon: AlertTriangle,
  },
  failed: {
    label: "Failed",
    tone: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
    icon: XCircle,
  },
  cancelled: {
    label: "Cancelled",
    tone: "bg-surface-3 text-muted-foreground",
    icon: CircleSlash,
  },
};

interface VerdictMeta {
  label: string;
  tone: string;
}

/** Visuals for an evaluator verdict (timeline rows). */
export const LOOP_VERDICT_META: Record<LoopVerdict, VerdictMeta> = {
  pass: {
    label: "Pass",
    tone: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  },
  fail: {
    label: "Fail",
    tone: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
  },
  needs_human: {
    label: "Needs human",
    tone: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  },
};

/** Human-readable summary of a terminal loop outcome (attempt badge). */
export const LOOP_OUTCOME_LABEL: Record<string, string> = {
  complete: "Goal met",
  capped: "Hit iteration cap",
  budget: "Hit resource budget",
  needs_human: "Escalated to human",
  cancelled: "Cancelled",
};
