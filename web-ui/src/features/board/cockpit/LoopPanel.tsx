import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleSlash,
  Coins,
  FileText,
  Gauge,
  Hash,
  ListChecks,
  Loader2,
  RotateCcw,
} from "@/components/icons";
import {
  useAckTaskLoop,
  useCancelTaskLoop,
  useTaskLoop,
  useTaskPlanning,
} from "@/api/hooks";
import type {
  AgentDTO,
  LoopAttemptDTO,
  LoopInfoDTO,
  LoopState,
  LoopTaskDTO,
  LoopTaskStatus,
  TaskDTO,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import { SelectMenu } from "@/components/ui/select-menu";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { useBoardEventListener } from "../BoardEventsContext";
import { GoalStepper, type GoalStage } from "./GoalStepper";
import { GoalTranscript } from "./GoalTranscript";
import { PlanStage, ReviewStage } from "./PlanningPanel";
import {
  LOOP_OUTCOME_LABEL,
  LOOP_STATE_META,
  LOOP_VERDICT_META,
} from "./loopStatus";

/** Live loop progress pushed over the board SSE feed (between API refetches). */
interface LiveStatus {
  state: LoopState;
  attempt: number;
  maxAttempts: number;
  totalTokens: number;
  outcome?: string | null;
}

/**
 * Subscribe to ``loop.status`` board events for one task. Returns the most
 * recent snapshot (so the panel updates instantly without waiting for the
 * React-Query refetch the same event triggers) plus a ``clear`` to drop it —
 * needed after an acknowledge, which clears the server state without emitting a
 * new ``loop.status`` event.
 */
function useLoopLiveStatus(taskId: string): [LiveStatus | null, () => void] {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  useBoardEventListener((e) => {
    if (e.type !== "loop.status" || e.task_id !== taskId) return;
    setStatus({
      state: (e.state as LoopState) ?? "running",
      attempt: e.attempt ?? 0,
      maxAttempts: e.max_attempts ?? 0,
      totalTokens: e.total_tokens ?? 0,
      outcome: e.outcome ?? null,
    });
  });
  // Drop a stale snapshot when switching tasks.
  useEffect(() => setStatus(null), [taskId]);
  const clear = useCallback(() => setStatus(null), []);
  return [status, clear];
}

const REVIEW_STATES: LoopState[] = [
  "waiting_plan_approval",
  "plan_approved",
  "plan_change_requested",
];

/** Map the persisted loop state (plus a restart intent) to one wizard stage. */
function stageFor(state: LoopState | null, restarting: boolean): GoalStage {
  if (state === "planning") return "plan";
  if (state && REVIEW_STATES.includes(state)) return "review";
  if (state === "running") return "run";
  // Terminal or unset: a restart sends the user back to drafting a new plan.
  if (restarting || !state) return "plan";
  return "result";
}

/**
 * The goal cockpit for a task, as one linear wizard: draft a plan, review and
 * approve it, run it, then read the verdict. Every goal is planned first —
 * there is no run-without-a-plan path.
 */
export function LoopPanel({
  task,
  agents,
  cliAgents,
  canEdit,
}: {
  task: TaskDTO;
  agents: AgentDTO[];
  cliAgents: AgentDTO[];
  canEdit: boolean;
}) {
  const loop = useTaskLoop(task.id);
  const planning = useTaskPlanning(task.id);
  const [live, clearLive] = useLoopLiveStatus(task.id);
  const cancel = useCancelTaskLoop(task.id);
  const ack = useAckTaskLoop(task.board_id, task.id);
  const [restarting, setRestarting] = useState(false);

  const info = loop.data;
  const pinfo = planning.data;
  const state: LoopState | null =
    live?.state ?? info?.loop_state ?? pinfo?.loop_state ?? null;
  const running = state === "running" || info?.is_running === true;
  const drafting = !!pinfo?.is_planning || state === "planning";
  const attempts = info?.attempts ?? [];
  const latestEval = useMemo(() => {
    for (let i = attempts.length - 1; i >= 0; i--) {
      const evals = attempts[i].evaluations;
      if (evals.length) return evals[evals.length - 1];
    }
    return null;
  }, [attempts]);

  // Once a fresh goal actually begins, drop the restart intent so the result
  // stage shows again when it finishes.
  useEffect(() => {
    if (state === "planning" || state === "running") setRestarting(false);
  }, [state]);

  const stage = stageFor(state, restarting);

  if (loop.isLoading && planning.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Spinner className="h-4 w-4" /> Loading goal…
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
      <div className="mx-auto max-w-3xl space-y-4 px-4 py-4">
        <GoalStepper current={stage} />

        {stage === "plan" && (
          <PlanStage
            task={task}
            agents={agents}
            cliAgents={cliAgents}
            canEdit={canEdit}
            drafting={drafting}
            lastError={pinfo?.last_error}
          />
        )}

        {stage === "review" && pinfo && (
          <ReviewStage
            task={task}
            agents={agents}
            cliAgents={cliAgents}
            canEdit={canEdit}
            info={pinfo}
          />
        )}

        {stage === "run" && state && (
          <>
            <StatusBanner state={state} live={live} info={info} />
            {running && canEdit && (
              <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2">
                <Loader2 className="h-4 w-4 animate-spin text-sky-500" />
                <span className="text-[13px] text-muted-foreground">
                  Working on the goal autonomously…
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  className="ml-auto"
                  onClick={() =>
                    cancel.mutate(undefined, {
                      onSuccess: (r) =>
                        r.ok
                          ? toast.success("Stopping after the current iteration")
                          : toast.message("No running goal to stop"),
                    })
                  }
                  disabled={cancel.isPending}
                >
                  <CircleSlash className="h-4 w-4" /> Stop
                </Button>
              </div>
            )}
          </>
        )}

        {stage === "result" && state && (
          <>
            <StatusBanner state={state} live={live} info={info} />
            {canEdit && (
              <ReviewActions
                state={state}
                missing={latestEval?.missing ?? ""}
                onRunAgain={() => setRestarting(true)}
                onAck={() => {
                  ack.mutate(undefined, {
                    // The ack clears server state without emitting a loop.status
                    // event, so drop the live SSE snapshot too — otherwise the
                    // banner would linger until the task is reopened.
                    onSuccess: () => clearLive(),
                    onError: (err) =>
                      toast.error(
                        err instanceof Error ? err.message : "Could not acknowledge",
                      ),
                  });
                }}
                acking={ack.isPending}
              />
            )}
          </>
        )}

        {/* Live task-graph progress (when executing task-by-task) */}
        {info?.tasks && info.tasks.length > 0 && (
          <TaskGraphProgress tasks={info.tasks} />
        )}

        {/* Full work transcripts for every role (plan / build / critic) */}
        {info && <GoalActivity taskId={task.id} info={info} running={running} />}

        {/* Iteration / evaluation timeline (run & result stages) */}
        {attempts.length > 0 && <AttemptTimeline attempts={attempts} />}
      </div>
    </div>
  );
}

function StatusBanner({
  state,
  live,
  info,
}: {
  state: LoopState;
  live: LiveStatus | null;
  info: { attempts: LoopAttemptDTO[]; objective?: string | null } | undefined;
}) {
  const meta = LOOP_STATE_META[state];
  const Icon = meta.icon;
  const attempt = live?.attempt ?? info?.attempts.length ?? 0;
  const maxAttempts = live?.maxAttempts ?? 0;
  const tokens = live?.totalTokens ?? 0;
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-lg border border-border px-3.5 py-2.5",
        meta.tone,
      )}
    >
      <Icon className={cn("h-5 w-5 shrink-0", meta.active && "animate-spin")} />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-semibold">Goal · {meta.label}</p>
        {info?.objective && (
          <p className="mt-0.5 truncate text-[12px] opacity-80" title={info.objective}>
            {info.objective}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-3 text-[12px] font-medium tabular-nums">
        {attempt > 0 && (
          <span className="inline-flex items-center gap-1" title="Iterations">
            <Hash className="h-3.5 w-3.5" />
            {attempt}
            {maxAttempts ? `/${maxAttempts}` : ""}
          </span>
        )}
        {tokens > 0 && (
          <span className="inline-flex items-center gap-1" title="Tokens used">
            <Coins className="h-3.5 w-3.5" />
            {tokens.toLocaleString()}
          </span>
        )}
      </div>
    </div>
  );
}

function ReviewActions({
  state,
  missing,
  onRunAgain,
  onAck,
  acking,
}: {
  state: LoopState;
  missing: string;
  onRunAgain: () => void;
  onAck: () => void;
  acking: boolean;
}) {
  const needsHuman = state === "waiting_for_human";
  return (
    <div className="rounded-lg border border-border bg-card p-3.5">
      <div className="flex items-start gap-2">
        {needsHuman ? (
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
        ) : state === "complete" ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
        ) : (
          <CircleSlash className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold text-foreground">
            {needsHuman
              ? "Needs a human decision"
              : state === "complete"
                ? "Goal verified complete"
                : state === "failed"
                  ? "The goal failed"
                  : "The goal was cancelled"}
          </p>
          {needsHuman && missing && (
            <p className="mt-1 whitespace-pre-wrap text-[12.5px] text-muted-foreground">
              {missing}
            </p>
          )}
          {needsHuman && !missing && (
            <p className="mt-1 text-[12.5px] text-muted-foreground">
              The goal stopped at a guardrail (iteration or resource cap) or
              asked for review. Inspect the latest iteration below, then plan a
              new goal or close.
            </p>
          )}
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onRunAgain}>
          <RotateCcw className="h-4 w-4" /> Plan a new goal
        </Button>
        <Button variant="ghost" size="sm" onClick={onAck} disabled={acking}>
          Acknowledge & close
        </Button>
      </div>
    </div>
  );
}

interface ActivitySource {
  id: string;
  label: string;
  conversationId: string;
}

/**
 * The work transcripts for every loop role, in one place. A source picker
 * switches between the planning phase, the agent's continuous build transcript
 * and each iteration's critic verification — so nothing the agents did is
 * hidden, and whichever role is running streams live.
 */
function GoalActivity({
  taskId,
  info,
  running,
}: {
  taskId: string;
  info: LoopInfoDTO;
  running: boolean;
}) {
  const [open, setOpen] = useState(true);

  const sources = useMemo<ActivitySource[]>(() => {
    const out: ActivitySource[] = [];
    if (info.planner_conversation_id) {
      out.push({
        id: "plan",
        label: "Plan",
        conversationId: info.planner_conversation_id,
      });
    }
    if (info.generator_conversation_id) {
      out.push({
        id: "build",
        label: "Build · agent",
        conversationId: info.generator_conversation_id,
      });
    }
    for (const a of info.attempts) {
      // The critic runs in its own fresh conversation per iteration; prefer the
      // attempt's critic run, falling back to the verdict's conversation.
      const conv =
        a.critic_conversation_id ??
        a.evaluations.find((e) => e.conversation_id)?.conversation_id;
      if (conv) {
        out.push({
          id: `critic-${a.id}`,
          label: `Critic · #${a.attempt_no}`,
          conversationId: conv,
        });
      }
    }
    return out;
  }, [info]);

  // Default to whatever role is streaming now, else the build transcript.
  const liveSourceId = useMemo(() => {
    if (!running || !info.active_conversation_id) return null;
    return (
      sources.find((s) => s.conversationId === info.active_conversation_id)?.id ??
      null
    );
  }, [running, info.active_conversation_id, sources]);

  const [selected, setSelected] = useState<string | null>(null);
  // Follow the live role as it advances (plan → build → critic), unless the
  // user has manually picked a source to inspect.
  const [pinned, setPinned] = useState(false);
  useEffect(() => {
    if (!pinned && liveSourceId) setSelected(liveSourceId);
  }, [liveSourceId, pinned]);

  if (sources.length === 0) return null;
  const current =
    sources.find((s) => s.id === selected) ??
    sources.find((s) => s.id === "build") ??
    sources[0];
  const isLive =
    running && info.active_conversation_id === current.conversationId;

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground"
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
          )}
          <Bot className="h-3.5 w-3.5 text-muted-foreground" /> Activity
        </button>
        {isLive && <Loader2 className="h-3 w-3 animate-spin text-sky-500" />}
        {open && sources.length > 1 && (
          <div className="ml-auto w-44">
            <SelectMenu
              value={current.id}
              onChange={(v) => {
                setSelected(v);
                setPinned(true);
              }}
              options={sources.map((s) => ({ value: s.id, label: s.label }))}
            />
          </div>
        )}
      </div>
      {open && (
        <div className="max-h-[60vh] overflow-auto border-t border-border scrollbar-thin">
          <GoalTranscript
            key={current.conversationId}
            taskId={taskId}
            conversationId={current.conversationId}
            activeRunId={isLive ? (info.active_run_id ?? null) : null}
            running={isLive}
          />
        </div>
      )}
    </div>
  );
}

const TASK_STATUS_META: Record<
  LoopTaskStatus,
  { icon: typeof Circle; cls: string; spin?: boolean }
> = {
  complete: { icon: CheckCircle2, cls: "text-emerald-500" },
  in_progress: { icon: Loader2, cls: "text-sky-500", spin: true },
  blocked: { icon: AlertTriangle, cls: "text-rose-500" },
  skipped: { icon: CircleSlash, cls: "text-muted-foreground" },
  pending: { icon: Circle, cls: "text-muted-foreground/50" },
};

/**
 * Live task-graph progress mirrored from ``TASKS.json``: each task with its
 * status, the currently-running one highlighted. The on-disk file is the source
 * of truth (updated by the orchestrator as each task verifies), polled via the
 * loop snapshot while running.
 */
function TaskGraphProgress({ tasks }: { tasks: LoopTaskDTO[] }) {
  const done = tasks.filter(
    (t) => t.status === "complete" || t.status === "skipped",
  ).length;
  return (
    <div>
      <div className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
        <ListChecks className="h-3.5 w-3.5 text-muted-foreground" /> Tasks
        <span className="ml-1 font-normal tabular-nums text-muted-foreground">
          {done}/{tasks.length} done
        </span>
      </div>
      <ul className="space-y-1">
        {tasks.map((t) => {
          const meta = TASK_STATUS_META[t.status] ?? TASK_STATUS_META.pending;
          const Icon = meta.icon;
          return (
            <li
              key={t.id}
              className={cn(
                "flex items-center gap-2 rounded-md border px-2.5 py-1.5",
                t.status === "in_progress"
                  ? "border-sky-300 bg-sky-50 dark:border-sky-500/30 dark:bg-sky-500/10"
                  : "border-border bg-card",
              )}
            >
              <Icon
                className={cn("h-4 w-4 shrink-0", meta.cls, meta.spin && "animate-spin")}
              />
              <span className="font-mono text-[11px] text-muted-foreground">
                {t.id}
              </span>
              <span
                className={cn(
                  "min-w-0 flex-1 truncate text-[12.5px]",
                  t.status === "complete"
                    ? "text-muted-foreground line-through"
                    : "text-foreground",
                )}
                title={t.title}
              >
                {t.title}
              </span>
              {t.status === "blocked" && (
                <span className="text-[10.5px] font-semibold uppercase tracking-[0.03em] text-rose-600 dark:text-rose-300">
                  blocked
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function AttemptTimeline({ attempts }: { attempts: LoopAttemptDTO[] }) {
  return (
    <div>
      <div className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
        <Gauge className="h-3.5 w-3.5 text-muted-foreground" /> Iterations
      </div>
      <ul className="space-y-2">
        {[...attempts].reverse().map((a) => {
          const verdict = a.evaluations[a.evaluations.length - 1];
          const vMeta = verdict ? LOOP_VERDICT_META[verdict.verdict] : null;
          return (
            <li
              key={a.id}
              className="rounded-lg border border-border bg-card px-3 py-2"
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-[12px] font-semibold text-foreground">
                  #{a.attempt_no}
                </span>
                {a.status === "running" && (
                  <span className="inline-flex items-center gap-1 text-[11px] text-sky-600 dark:text-sky-300">
                    <Loader2 className="h-3 w-3 animate-spin" /> running
                  </span>
                )}
                {vMeta && (
                  <span
                    className={cn(
                      "rounded-sm px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.03em]",
                      vMeta.tone,
                    )}
                  >
                    {vMeta.label}
                  </span>
                )}
                {verdict && <ScoreStars score={verdict.score} />}
                {a.outcome && (
                  <span className="ml-auto text-[11px] font-medium text-muted-foreground">
                    {LOOP_OUTCOME_LABEL[a.outcome] ?? a.outcome}
                  </span>
                )}
              </div>
              {verdict && <VerdictDetail verdict={verdict} />}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** A 5-star rating derived from the critic's 0–1 score, with the % alongside. */
function ScoreStars({ score }: { score: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, score)) * 100);
  const filled = Math.round((pct / 100) * 5);
  return (
    <span
      className="ml-auto inline-flex items-center gap-1 text-[11px] font-medium tabular-nums text-muted-foreground"
      title={`Critic score: ${pct}%`}
    >
      <span aria-hidden className="tracking-[1px] text-amber-500">
        {"★".repeat(filled)}
        <span className="text-muted-foreground/30">{"★".repeat(5 - filled)}</span>
      </span>
      {pct}%
    </span>
  );
}

/**
 * The richer body of one critic verdict: what's still missing and the evidence
 * the critic gathered (commands it ran, checks it saw). Evidence is collapsed
 * behind a disclosure so the timeline stays scannable.
 */
function VerdictDetail({
  verdict,
}: {
  verdict: import("@/api/types").LoopEvaluationDTO;
}) {
  const [open, setOpen] = useState(false);
  const [rawOpen, setRawOpen] = useState(false);
  const evidence = verdict.evidence ?? {};
  const evidenceRows = useMemo(
    () =>
      Object.entries(evidence)
        .map(([k, v]) => [k, stringifyEvidence(v)] as const)
        .filter(([, v]) => v.trim().length > 0),
    [evidence],
  );
  // Reconstruct the canonical verdict JSON the critic wrote to its verdict file.
  // The file is consumed and deleted after grading, but its content is persisted
  // (verdict / score / missing / evidence), so this is a faithful equivalent.
  const verdictJson = useMemo(
    () =>
      JSON.stringify(
        {
          verdict: verdict.verdict,
          score: verdict.score,
          missing: verdict.missing,
          evidence,
        },
        null,
        2,
      ),
    [verdict.verdict, verdict.score, verdict.missing, evidence],
  );
  return (
    <>
      {verdict.missing && (
        <p className="mt-1.5 whitespace-pre-wrap text-[12px] text-muted-foreground">
          {verdict.missing}
        </p>
      )}
      {evidenceRows.length > 0 && (
        <div className="mt-1.5">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground"
          >
            <ListChecks className="h-3 w-3" />
            {open ? "Hide evidence" : "Show evidence"}
          </button>
          {open && (
            <dl className="mt-1.5 space-y-1.5 rounded-md border border-dashed border-border bg-surface-1/40 p-2.5">
              {evidenceRows.map(([key, value]) => (
                <div key={key}>
                  <dt className="text-[10.5px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
                    {key}
                  </dt>
                  <dd className="mt-0.5 whitespace-pre-wrap break-words font-mono text-[11.5px] text-foreground/90">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}
      <div className="mt-1.5">
        <button
          type="button"
          onClick={() => setRawOpen((v) => !v)}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground"
        >
          <FileText className="h-3 w-3" />
          {rawOpen ? "Hide verdict JSON" : "Show verdict JSON"}
        </button>
        {rawOpen && (
          <pre className="mt-1.5 max-h-72 overflow-auto rounded-md border border-dashed border-border bg-surface-1/40 p-2.5 font-mono text-[11px] leading-relaxed text-foreground/90">
            {verdictJson}
          </pre>
        )}
      </div>
    </>
  );
}

/** Render one evidence value (string as-is, objects/arrays as compact JSON). */
function stringifyEvidence(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
