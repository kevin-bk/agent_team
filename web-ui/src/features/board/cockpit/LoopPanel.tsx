import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleSlash,
  Coins,
  Gauge,
  Hash,
  ListChecks,
  Loader2,
  Play,
  RotateCcw,
  Sparkles,
} from "@/components/icons";
import {
  useAckTaskLoop,
  useCancelTaskLoop,
  useStartTaskLoop,
  useTaskLoop,
} from "@/api/hooks";
import type {
  AgentDTO,
  LoopAttemptDTO,
  LoopState,
  TaskDTO,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SelectMenu } from "@/components/ui/select-menu";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { useBoardEventListener } from "../BoardEventsContext";
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
 * recent snapshot, so the panel updates instantly without waiting for the
 * React-Query refetch the same event triggers.
 */
function useLoopLiveStatus(taskId: string): LiveStatus | null {
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
  return status;
}

const TERMINAL_STATES: LoopState[] = [
  "complete",
  "failed",
  "cancelled",
  "waiting_for_human",
];

/**
 * The autonomous-loop cockpit for a task: start a generator+evaluator loop,
 * watch its live progress, review a stop that needs a human, and inspect the
 * attempt/evaluation history.
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
  const live = useLoopLiveStatus(task.id);
  const start = useStartTaskLoop(task.board_id, task.id);
  const cancel = useCancelTaskLoop(task.id);
  const ack = useAckTaskLoop(task.board_id, task.id);

  const info = loop.data;
  // Prefer the live SSE state (freshest) over the persisted one.
  const state: LoopState | null = live?.state ?? info?.loop_state ?? null;
  const running = state === "running" || info?.is_running === true;
  const attempts = info?.attempts ?? [];
  const latestEval = useMemo(() => {
    for (let i = attempts.length - 1; i >= 0; i--) {
      const evals = attempts[i].evaluations;
      if (evals.length) return evals[evals.length - 1];
    }
    return null;
  }, [attempts]);

  const [showForm, setShowForm] = useState(false);
  // Show the start form by default only for a task that has never run a loop.
  const hasHistory = attempts.length > 0 || !!state;
  const formOpen = showForm || (!hasHistory && canEdit);

  if (loop.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Spinner className="h-4 w-4" /> Loading loop…
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
      <div className="mx-auto max-w-3xl space-y-4 px-4 py-4">
        {/* Live status banner */}
        {state && <StatusBanner state={state} live={live} info={info} />}

        {/* Human-review / terminal actions */}
        {state &&
          TERMINAL_STATES.includes(state) &&
          !running &&
          canEdit && (
            <ReviewActions
              state={state}
              missing={latestEval?.missing ?? ""}
              onRunAgain={() => setShowForm(true)}
              onAck={() => {
                ack.mutate(undefined, {
                  onError: (err) =>
                    toast.error(
                      err instanceof Error ? err.message : "Could not acknowledge",
                    ),
                });
              }}
              acking={ack.isPending}
            />
          )}

        {/* Running → offer cancel */}
        {running && canEdit && (
          <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2">
            <Loader2 className="h-4 w-4 animate-spin text-sky-500" />
            <span className="text-[13px] text-muted-foreground">
              The loop is working autonomously…
            </span>
            <Button
              variant="outline"
              size="sm"
              className="ml-auto"
              onClick={() =>
                cancel.mutate(undefined, {
                  onSuccess: (r) =>
                    r.ok
                      ? toast.success("Stopping after the current attempt")
                      : toast.message("No running loop to stop"),
                })
              }
              disabled={cancel.isPending}
            >
              <CircleSlash className="h-4 w-4" /> Stop
            </Button>
          </div>
        )}

        {/* Start form */}
        {canEdit && formOpen && !running && (
          <StartForm
            task={task}
            agents={agents}
            cliAgents={cliAgents}
            pending={start.isPending}
            onCancel={hasHistory ? () => setShowForm(false) : undefined}
            onStart={(body) => {
              start.mutate(body, {
                onSuccess: () => {
                  setShowForm(false);
                  toast.success("Autonomous loop started");
                },
                onError: (err) =>
                  toast.error(
                    err instanceof Error ? err.message : "Could not start the loop",
                  ),
              });
            }}
          />
        )}

        {/* Idle with history but form closed → quick start button */}
        {canEdit && !formOpen && !running && hasHistory && (
          <Button variant="outline" size="sm" onClick={() => setShowForm(true)}>
            <Play className="h-4 w-4" /> Start a new loop
          </Button>
        )}

        {/* Attempt / evaluation timeline */}
        {attempts.length > 0 && <AttemptTimeline attempts={attempts} />}

        {!canEdit && !state && (
          <p className="text-[13px] text-muted-foreground">
            No autonomous loop has run on this task yet.
          </p>
        )}
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
        <p className="text-[13px] font-semibold">Autonomous loop · {meta.label}</p>
        {info?.objective && (
          <p className="mt-0.5 truncate text-[12px] opacity-80" title={info.objective}>
            {info.objective}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-3 text-[12px] font-medium tabular-nums">
        {attempt > 0 && (
          <span className="inline-flex items-center gap-1" title="Attempts">
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
                ? "Objective verified complete"
                : state === "failed"
                  ? "The loop failed"
                  : "The loop was cancelled"}
          </p>
          {needsHuman && missing && (
            <p className="mt-1 whitespace-pre-wrap text-[12.5px] text-muted-foreground">
              {missing}
            </p>
          )}
          {needsHuman && !missing && (
            <p className="mt-1 text-[12.5px] text-muted-foreground">
              The loop stopped at a guardrail (attempt or resource cap) or asked
              for review. Inspect the latest attempt below, then continue or close.
            </p>
          )}
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onRunAgain}>
          <RotateCcw className="h-4 w-4" /> Run again
        </Button>
        <Button variant="ghost" size="sm" onClick={onAck} disabled={acking}>
          Acknowledge & close
        </Button>
      </div>
    </div>
  );
}

function StartForm({
  task,
  agents,
  cliAgents,
  pending,
  onStart,
  onCancel,
}: {
  task: TaskDTO;
  agents: AgentDTO[];
  cliAgents: AgentDTO[];
  pending: boolean;
  onStart: (body: {
    agent_id: string;
    evaluator_id: string;
    objective: string;
    planner_id: string | null;
    max_attempts: number;
    max_tokens: number | null;
    max_cost_usd: number | null;
    max_wall_seconds: number | null;
  }) => void;
  onCancel?: () => void;
}) {
  // Both roles accept any staffed agent or direct CLI. A direct CLI makes a
  // strong evaluator: it can run the project's tests/lint/build in the
  // workspace to verify completion (not just read a transcript), and the
  // verdict parser tolerates a JSON object embedded anywhere in its output.
  const generatorOptions = useMemo(
    () => [...agents, ...cliAgents],
    [agents, cliAgents],
  );
  const evaluatorOptions = generatorOptions;

  const [generator, setGenerator] = useState(generatorOptions[0]?.id ?? "");
  // Default the evaluator to an agent distinct from the generator when possible
  // (independent grading), else the first available option.
  const [evaluator, setEvaluator] = useState(
    evaluatorOptions.find((a) => a.id !== generatorOptions[0]?.id)?.id ??
      evaluatorOptions[0]?.id ??
      "",
  );
  const [objective, setObjective] = useState(
    task.objective || task.description || "",
  );
  // Optional planning phase: an agent analyses the task and writes a plan the
  // generator then works from. Defaults to the generator agent.
  const [planFirst, setPlanFirst] = useState(false);
  const [planner, setPlanner] = useState(generatorOptions[0]?.id ?? "");
  const [maxAttempts, setMaxAttempts] = useState("10");
  const [maxTokens, setMaxTokens] = useState("");
  const [maxCost, setMaxCost] = useState("");
  const [maxMinutes, setMaxMinutes] = useState("");

  const toOpt = (a: AgentDTO) => ({
    value: a.id,
    label: a.display_name,
    icon: <Bot className="h-3.5 w-3.5" />,
    description: a.id.startsWith("cli:") ? "Direct CLI" : a.model ?? "agent",
  });

  const canStart = !!generator && !!evaluator && !!objective.trim() && !pending;
  const num = (s: string): number | null => {
    const n = Number(s.replace(/[, ]/g, ""));
    return Number.isFinite(n) && n > 0 ? n : null;
  };

  return (
    <div className="rounded-lg border border-border bg-card p-3.5">
      <div className="mb-3 flex items-center gap-1.5">
        <Sparkles className="h-4 w-4 text-primary" />
        <span className="text-[13px] font-semibold text-foreground">
          Start an autonomous loop
        </span>
      </div>
      <p className="mb-3 text-[12px] text-muted-foreground">
        The generator works the task; an independent evaluator grades each
        attempt and the loop repeats until the objective is verified or a
        guardrail routes it to you for review.
      </p>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Generator agent">
          <SelectMenu
            value={generator}
            onChange={setGenerator}
            options={generatorOptions.map(toOpt)}
            placeholder="Pick an agent"
          />
        </Field>
        <Field label="Evaluator agent">
          <SelectMenu
            value={evaluator}
            onChange={setEvaluator}
            options={evaluatorOptions.map(toOpt)}
            placeholder="Pick an evaluator"
          />
        </Field>
      </div>

      <Field label="Objective" className="mt-3">
        <textarea
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          rows={3}
          placeholder="What does 'done' mean? Include the acceptance criteria the evaluator should verify."
          className="block w-full resize-y rounded border border-input bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/50 focus:border-[#4C9AFF] focus:outline-none"
        />
      </Field>

      {/* Optional planning phase */}
      <div className="mt-3 rounded-lg border border-border bg-surface-1/40 p-3">
        <label className="flex cursor-pointer items-start gap-2">
          <input
            type="checkbox"
            checked={planFirst}
            onChange={(e) => setPlanFirst(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-input accent-primary"
          />
          <span className="min-w-0">
            <span className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
              <ListChecks className="h-3.5 w-3.5 text-indigo-500" />
              Plan first
            </span>
            <span className="mt-0.5 block text-[12px] text-muted-foreground">
              A planner agent analyses the task and writes a structured{" "}
              <code className="font-mono text-[11px]">PLAN.md</code> before any
              work; the generator then implements that plan.
            </span>
          </span>
        </label>
        {planFirst && (
          <Field label="Planner agent" className="mt-3">
            <SelectMenu
              value={planner}
              onChange={setPlanner}
              options={generatorOptions.map(toOpt)}
              placeholder="Pick a planner"
            />
          </Field>
        )}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="Max attempts">
          <Input
            value={maxAttempts}
            onChange={(e) => setMaxAttempts(e.target.value)}
            inputMode="numeric"
          />
        </Field>
        <Field label="Max tokens" hint="optional">
          <Input
            value={maxTokens}
            onChange={(e) => setMaxTokens(e.target.value)}
            inputMode="numeric"
            placeholder="∞"
          />
        </Field>
        <Field label="Max cost $" hint="optional">
          <Input
            value={maxCost}
            onChange={(e) => setMaxCost(e.target.value)}
            inputMode="decimal"
            placeholder="∞"
          />
        </Field>
        <Field label="Max minutes" hint="optional">
          <Input
            value={maxMinutes}
            onChange={(e) => setMaxMinutes(e.target.value)}
            inputMode="numeric"
            placeholder="∞"
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center gap-2">
        <Button
          size="sm"
          disabled={!canStart}
          onClick={() =>
            onStart({
              agent_id: generator,
              evaluator_id: evaluator,
              objective: objective.trim(),
              planner_id: planFirst ? planner || generator : null,
              max_attempts: Math.max(1, Math.min(100, Number(maxAttempts) || 10)),
              max_tokens: num(maxTokens),
              max_cost_usd: num(maxCost),
              max_wall_seconds: maxMinutes ? (num(maxMinutes) ?? 0) * 60 : null,
            })
          }
        >
          {pending ? (
            <Spinner className="h-4 w-4" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          Start loop
        </Button>
        {onCancel && (
          <Button variant="ghost" size="sm" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  className,
  children,
}: {
  label: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={cn("block", className)}>
      <span className="mb-1 flex items-center gap-1 text-[12px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
        {label}
        {hint && <span className="font-normal normal-case opacity-70">· {hint}</span>}
      </span>
      {children}
    </label>
  );
}

function AttemptTimeline({ attempts }: { attempts: LoopAttemptDTO[] }) {
  return (
    <div>
      <div className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
        <Gauge className="h-3.5 w-3.5 text-muted-foreground" /> Attempts
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
                    {verdict && verdict.score > 0
                      ? ` · ${Math.round(verdict.score * 100)}%`
                      : ""}
                  </span>
                )}
                {a.outcome && (
                  <span className="ml-auto text-[11px] font-medium text-muted-foreground">
                    {LOOP_OUTCOME_LABEL[a.outcome] ?? a.outcome}
                  </span>
                )}
              </div>
              {verdict?.missing && (
                <p className="mt-1.5 whitespace-pre-wrap text-[12px] text-muted-foreground">
                  {verdict.missing}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
