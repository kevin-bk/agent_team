import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  HelpCircle,
  ListChecks,
  Loader2,
  Pencil,
  Play,
  Send,
} from "@/components/icons";
import {
  useAnswerTaskPlanning,
  useApproveAndRunTaskPlanning,
  useEditTaskPlanningArtifact,
  useRequestTaskPlanningChanges,
  useStartTaskPlanning,
} from "@/api/hooks";
import type {
  AgentDTO,
  PlanningArtifactDTO,
  PlanningInfoDTO,
  PlanningQuestion,
  TaskDTO,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SelectMenu } from "@/components/ui/select-menu";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

function agentOpt(a: AgentDTO) {
  return {
    value: a.id,
    label: a.display_name,
    icon: <Bot className="h-3.5 w-3.5" />,
    description: a.id.startsWith("cli:") ? "Direct CLI" : a.model ?? "agent",
  };
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
      <span className="mb-1 flex items-center gap-1 text-[11px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
        {label}
        {hint && <span className="font-normal normal-case opacity-70">· {hint}</span>}
      </span>
      {children}
    </label>
  );
}

/* ─────────────────────────────  PLAN (stage 1)  ───────────────────────── */

/**
 * Stage 1 of a goal: either the setup form (draft a plan from an objective) or,
 * while the planner is researching, a drafting indicator. Every goal starts
 * here — there is no "run without a plan" path.
 */
export function PlanStage({
  task,
  agents,
  cliAgents,
  canEdit,
  drafting,
  lastError,
}: {
  task: TaskDTO;
  agents: AgentDTO[];
  cliAgents: AgentDTO[];
  canEdit: boolean;
  drafting: boolean;
  lastError?: string | null;
}) {
  if (drafting) {
    return (
      <div className="rounded-lg border border-border bg-card p-3.5">
        <div className="flex items-center gap-2 text-[13px] text-foreground">
          <Loader2 className="h-4 w-4 animate-spin text-indigo-500" />
          <span className="font-semibold">Drafting the plan</span>
        </div>
        <p className="mt-1 text-[12px] text-muted-foreground">
          The planner is researching the workspace and writing the SPEC, PLAN and
          task list. You will review and approve before any code is written.
        </p>
        {lastError && <ErrorNote>{lastError}</ErrorNote>}
      </div>
    );
  }
  if (!canEdit) {
    return (
      <p className="text-[13px] text-muted-foreground">
        No goal has been planned for this task yet.
      </p>
    );
  }
  return <PlanSetupForm task={task} agents={agents} cliAgents={cliAgents} lastError={lastError} />;
}

function PlanSetupForm({
  task,
  agents,
  cliAgents,
  lastError,
}: {
  task: TaskDTO;
  agents: AgentDTO[];
  cliAgents: AgentDTO[];
  lastError?: string | null;
}) {
  const options = useMemo(() => [...agents, ...cliAgents], [agents, cliAgents]);
  const [planner, setPlanner] = useState(options[0]?.id ?? "");
  const [reviewer, setReviewer] = useState("");
  const [objective, setObjective] = useState(
    task.objective || task.description || "",
  );
  const start = useStartTaskPlanning(task.board_id, task.id);

  return (
    <div className="rounded-lg border border-border bg-card p-3.5">
      <div className="mb-1 flex items-center gap-1.5">
        <ListChecks className="h-4 w-4 text-indigo-500" />
        <span className="text-[13px] font-semibold text-foreground">
          Draft a plan
        </span>
      </div>
      <p className="mb-3 text-[12px] text-muted-foreground">
        A planner agent researches the task and writes a SPEC, PLAN and task list.
        You review and approve it before the build starts.
      </p>

      <Field label="Objective">
        <textarea
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          rows={3}
          placeholder="What does 'done' mean? The planner turns this into a precise spec the build is graded against."
          className="block w-full resize-y rounded border border-input bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/50 focus:border-[#4C9AFF] focus:outline-none"
        />
      </Field>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="Planner agent">
          <SelectMenu
            value={planner}
            onChange={setPlanner}
            options={options.map(agentOpt)}
            placeholder="Pick a planner"
          />
        </Field>
        <Field label="Reviewer" hint="optional">
          <SelectMenu
            value={reviewer}
            onChange={setReviewer}
            options={[
              { value: "", label: "No reviewer" },
              ...options.map(agentOpt),
            ]}
            placeholder="No reviewer"
          />
        </Field>
      </div>

      {lastError && <ErrorNote>{lastError}</ErrorNote>}

      <div className="mt-3">
        <Button
          size="sm"
          disabled={!planner || !objective.trim() || start.isPending}
          onClick={() =>
            start.mutate(
              { planner_id: planner, reviewer_id: reviewer || null, objective },
              {
                onSuccess: () => toast.success("Planning started"),
                onError: (err) =>
                  toast.error(
                    err instanceof Error ? err.message : "Could not start",
                  ),
              },
            )
          }
        >
          {start.isPending ? (
            <Spinner className="h-4 w-4" />
          ) : (
            <ListChecks className="h-4 w-4" />
          )}
          Draft plan
        </Button>
      </div>
    </div>
  );
}

/* ────────────────────────────  REVIEW (stage 2)  ──────────────────────── */

/**
 * Stage 2 of a goal: the drafted plan is parked for a human. Review/edit the
 * SPEC/PLAN/TASKS artifacts, then either approve & run, or send the planner
 * feedback (which returns the goal to the planning stage to re-draft).
 */
/**
 * Read-only list of the NON-blocking questions an agent noted while planning.
 * These never pause the build (the agent picked a safe default), so they never
 * reach the answer panel — but surfacing them here lets a human catch a wrong
 * assumption and "Request changes" instead of discovering it after execution.
 */
function NotedQuestions({ questions }: { questions: PlanningQuestion[] }) {
  const noted = useMemo(
    () => questions.filter((q) => q.blocking === false),
    [questions],
  );
  if (noted.length === 0) return null;
  return (
    <div className="mt-2 rounded-md border border-sky-300 bg-sky-50 p-2.5 dark:border-sky-500/30 dark:bg-sky-500/10">
      <div className="mb-1 flex items-center gap-1.5 text-[12px] font-semibold text-sky-800 dark:text-sky-300">
        <HelpCircle className="h-3.5 w-3.5" />
        The agent noted {noted.length} assumption{noted.length > 1 ? "s" : ""}{" "}
        (non-blocking)
      </div>
      <p className="mb-2 text-[11.5px] text-sky-800/80 dark:text-sky-300/80">
        It proceeded with a safe default for each. If one is wrong, use “Request
        changes” below to redirect the planner.
      </p>
      <ul className="space-y-2">
        {noted.map((q) => (
          <li
            key={q.id}
            className="rounded border border-sky-200/70 bg-card/60 p-2 dark:border-sky-500/20"
          >
            <div className="text-[12.5px] font-medium text-foreground">
              {q.question}
            </div>
            {q.reason && (
              <div className="mt-0.5 text-[11.5px] text-muted-foreground">
                {q.reason}
              </div>
            )}
            {q.options && q.options.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {q.options.map((opt) => (
                  <span
                    key={opt}
                    className="rounded bg-surface-1 px-1.5 py-0.5 text-[11px] text-muted-foreground"
                  >
                    {opt}
                  </span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ReviewStage({
  task,
  agents,
  cliAgents,
  canEdit,
  info,
}: {
  task: TaskDTO;
  agents: AgentDTO[];
  cliAgents: AgentDTO[];
  canEdit: boolean;
  info: PlanningInfoDTO;
}) {
  const editable = useMemo(
    () =>
      info.artifacts.filter((a) =>
        ["SPEC.md", "PLAN.md", "TASKS.json"].includes(
          a.path.split("/").pop() ?? "",
        ),
      ),
    [info.artifacts],
  );
  // Set when the build paused because the agent flagged the approved plan as
  // wrong/unsafe — its reasoning is shown so the human can revise and re-run.
  const changeRequest = useMemo(
    () =>
      info.artifacts.find(
        (a) =>
          (a.path.split("/").pop() ?? "") === "PLAN_CHANGE_REQUEST.md" &&
          a.exists &&
          a.content,
      )?.content ?? null,
    [info.artifacts],
  );

  return (
    <div className="rounded-lg border border-border bg-card p-3.5">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <ListChecks className="h-4 w-4 text-indigo-500" />
        <span className="text-[13px] font-semibold text-foreground">
          Review the plan
        </span>
        {info.review_verdict && (
          <span
            className={cn(
              "ml-1 rounded px-1.5 py-0.5 text-[11px] font-medium",
              info.review_verdict === "pass"
                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
                : info.review_verdict === "needs_human"
                  ? "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
                  : "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
            )}
          >
            Reviewer: {info.review_verdict}
          </span>
        )}
      </div>

      {info.last_error && <ErrorNote>{info.last_error}</ErrorNote>}

      {changeRequest && (
        <div className="mt-2 rounded-md border border-amber-300 bg-amber-50 p-2.5 dark:border-amber-500/30 dark:bg-amber-500/10">
          <div className="mb-1 flex items-center gap-1.5 text-[12px] font-semibold text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5" />
            The agent paused and requested a plan change
          </div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-amber-900/90 dark:text-amber-200/90">
            {changeRequest}
          </pre>
          <p className="mt-1.5 text-[11.5px] text-amber-800/80 dark:text-amber-300/80">
            Revise the plan below then Approve &amp; run again, or send the
            planner feedback to re-draft.
          </p>
        </div>
      )}

      <NotedQuestions questions={info.questions ?? []} />

      <ArtifactTabs taskId={task.id} artifacts={editable} canEdit={canEdit} />

      {canEdit && (
        <ApprovalBar task={task} info={info} agents={agents} cliAgents={cliAgents} />
      )}
    </div>
  );
}

function ArtifactTabs({
  taskId,
  artifacts,
  canEdit,
}: {
  taskId: string;
  artifacts: PlanningArtifactDTO[];
  canEdit: boolean;
}) {
  const present = artifacts.filter((a) => a.exists);
  const [active, setActive] = useState(0);
  if (present.length === 0) {
    return (
      <p className="mt-2 rounded border border-dashed border-border px-3 py-4 text-center text-[12px] text-muted-foreground">
        No plan artifacts were written.
      </p>
    );
  }
  const idx = Math.min(active, present.length - 1);
  const current = present[idx];
  return (
    <div className="mt-2 overflow-hidden rounded-md border border-border">
      <div className="flex items-center gap-1 border-b border-border bg-surface-1/50 px-1.5 py-1">
        {present.map((a, i) => {
          const name = a.path.split("/").pop() ?? a.path;
          return (
            <button
              key={a.path}
              type="button"
              onClick={() => setActive(i)}
              className={cn(
                "rounded px-2.5 py-1 text-[12px] font-medium transition-colors",
                i === idx
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {name}
            </button>
          );
        })}
      </div>
      <ArtifactPane key={current.path} taskId={taskId} artifact={current} canEdit={canEdit} />
    </div>
  );
}

function ArtifactPane({
  taskId,
  artifact,
  canEdit,
}: {
  taskId: string;
  artifact: PlanningArtifactDTO;
  canEdit: boolean;
}) {
  const name = artifact.path.split("/").pop() ?? artifact.path;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(artifact.content ?? "");
  const edit = useEditTaskPlanningArtifact(taskId);

  return (
    <div className="p-2">
      {canEdit && (
        <div className="mb-1.5 flex justify-end">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2"
            onClick={() => {
              setDraft(artifact.content ?? "");
              setEditing((v) => !v);
            }}
          >
            <Pencil className="h-3 w-3" /> {editing ? "Cancel" : "Edit"}
          </Button>
        </div>
      )}
      {editing ? (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={16}
            className="block w-full resize-y rounded border border-input bg-surface-1 px-2.5 py-1.5 font-mono text-[11.5px] leading-relaxed text-foreground focus:border-primary focus:outline-none"
          />
          <div className="mt-2 flex justify-end">
            <Button
              size="sm"
              disabled={edit.isPending}
              onClick={() =>
                edit.mutate(
                  { name, content: draft, etag: artifact.etag },
                  {
                    onSuccess: () => {
                      setEditing(false);
                      toast.success(`${name} saved`);
                    },
                    onError: (err) =>
                      toast.error(
                        err instanceof Error ? err.message : "Save failed",
                      ),
                  },
                )
              }
            >
              {edit.isPending ? <Spinner className="h-4 w-4" /> : null} Save
            </Button>
          </div>
        </>
      ) : (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-muted-foreground">
          {artifact.content}
        </pre>
      )}
    </div>
  );
}

function ApprovalBar({
  task,
  info,
  agents,
  cliAgents,
}: {
  task: TaskDTO;
  info: PlanningInfoDTO;
  agents: AgentDTO[];
  cliAgents: AgentDTO[];
}) {
  const options = useMemo(() => [...agents, ...cliAgents], [agents, cliAgents]);
  const [generator, setGenerator] = useState(options[0]?.id ?? "");
  const [evaluator, setEvaluator] = useState(
    options.find((a) => a.id !== options[0]?.id)?.id ?? options[0]?.id ?? "",
  );
  const [maxAttempts, setMaxAttempts] = useState("10");
  const [maxTokens, setMaxTokens] = useState("");
  const [maxCost, setMaxCost] = useState("");
  const [maxMinutes, setMaxMinutes] = useState("");
  const [showFeedback, setShowFeedback] = useState(false);
  const [feedback, setFeedback] = useState("");

  // How many tasks the planner wrote into TASKS.json — drives whether the
  // task-by-task option is offered (and the per-task wording).
  const taskCount = useMemo(() => {
    const raw = info.artifacts.find(
      (a) => (a.path.split("/").pop() ?? "") === "TASKS.json" && a.content,
    )?.content;
    if (!raw) return 0;
    try {
      const parsed = JSON.parse(raw) as { tasks?: unknown[] };
      return Array.isArray(parsed.tasks) ? parsed.tasks.length : 0;
    } catch {
      return 0;
    }
  }, [info.artifacts]);
  const [taskByTask, setTaskByTask] = useState(true);
  const useGraph = taskCount > 0 && taskByTask;

  const run = useApproveAndRunTaskPlanning(task.board_id, task.id);
  const requestChanges = useRequestTaskPlanningChanges(task.id);

  const posInt = (s: string): number | null => {
    const n = Number(s.replace(/[, ]/g, ""));
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : null;
  };

  return (
    <div className="mt-3 space-y-3 border-t border-border pt-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field label="Agent">
          <SelectMenu
            value={generator}
            onChange={setGenerator}
            options={options.map(agentOpt)}
            placeholder="Agent"
          />
        </Field>
        <Field label="Critic">
          <SelectMenu
            value={evaluator}
            onChange={setEvaluator}
            options={options.map(agentOpt)}
            placeholder="Critic"
          />
        </Field>
        <Field label={useGraph ? "Attempts / task" : "Max iterations"}>
          <Input
            value={maxAttempts}
            onChange={(e) => setMaxAttempts(e.target.value)}
            inputMode="numeric"
          />
        </Field>
      </div>

      {taskCount > 0 && (
        <label className="flex items-start gap-2 rounded-md border border-border bg-surface-1/40 p-2.5 text-[12.5px]">
          <input
            type="checkbox"
            checked={taskByTask}
            onChange={(e) => setTaskByTask(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-primary"
          />
          <span>
            <span className="font-semibold text-foreground">
              Execute task-by-task
            </span>{" "}
            <span className="text-muted-foreground">
              ({taskCount} tasks) — schedule by dependency, verify each task
              before the next. Uncheck to run one loop over the whole objective.
            </span>
          </span>
        </label>
      )}
      <div className="grid grid-cols-3 gap-3">
        <Field label="Max tokens" hint="opt">
          <Input
            value={maxTokens}
            onChange={(e) => setMaxTokens(e.target.value)}
            inputMode="numeric"
            placeholder="∞"
          />
        </Field>
        <Field label="Max cost $" hint="opt">
          <Input
            value={maxCost}
            onChange={(e) => setMaxCost(e.target.value)}
            inputMode="decimal"
            placeholder="∞"
          />
        </Field>
        <Field label="Max minutes" hint="opt">
          <Input
            value={maxMinutes}
            onChange={(e) => setMaxMinutes(e.target.value)}
            inputMode="numeric"
            placeholder="∞"
          />
        </Field>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={!generator || !evaluator || run.isPending}
          onClick={() =>
            run.mutate(
              {
                agent_id: generator,
                evaluator_id: evaluator,
                task_graph: useGraph,
                max_attempts: Math.max(1, Math.min(100, posInt(maxAttempts) ?? 10)),
                max_tokens: posInt(maxTokens),
                max_cost_usd: (() => {
                  const n = Number(maxCost.replace(/[, ]/g, ""));
                  return Number.isFinite(n) && n > 0 ? n : null;
                })(),
                max_wall_seconds: maxMinutes
                  ? (posInt(maxMinutes) ?? 0) * 60
                  : null,
              },
              {
                onSuccess: () => toast.success("Approved — running the plan"),
                onError: (err) =>
                  toast.error(
                    err instanceof Error ? err.message : "Could not run",
                  ),
              },
            )
          }
        >
          {run.isPending ? (
            <Spinner className="h-4 w-4" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          Approve &amp; run
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={requestChanges.isPending}
          onClick={() => setShowFeedback((v) => !v)}
        >
          <Pencil className="h-4 w-4" /> Request changes
        </Button>
        {info.approved && (
          <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600 dark:text-emerald-300">
            <CheckCircle2 className="h-3.5 w-3.5" /> Approved
          </span>
        )}
      </div>

      {showFeedback && (
        <div className="rounded-md border border-border bg-surface-1/40 p-2.5">
          <p className="mb-1.5 text-[12px] text-muted-foreground">
            Tell the planner what to change. It re-drafts the plan and returns
            here for review.
          </p>
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            rows={3}
            placeholder="e.g. The plan misses the migration step; also split task 2 into smaller units."
            className="block w-full resize-y rounded border border-input bg-card px-2.5 py-1.5 text-[12.5px] text-foreground placeholder:text-muted-foreground/50 focus:border-primary focus:outline-none"
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setShowFeedback(false);
                setFeedback("");
              }}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={requestChanges.isPending}
              onClick={() =>
                requestChanges.mutate(feedback.trim() || undefined, {
                  onSuccess: () => {
                    setShowFeedback(false);
                    setFeedback("");
                    toast.success("Re-drafting the plan");
                  },
                  onError: (err) =>
                    toast.error(
                      err instanceof Error ? err.message : "Could not request",
                    ),
                })
              }
            >
              {requestChanges.isPending ? (
                <Spinner className="h-4 w-4" />
              ) : (
                <Send className="h-4 w-4" />
              )}
              Send to planner
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ───────────────────────────  QUESTIONS (pause)  ──────────────────────── */

const OTHER = "__other__";

/**
 * The agent (planner or generator) raised blocking questions and paused. Show
 * one card per question — suggested options plus an always-present "Other"
 * free-text choice — and an overall note. Answering resumes the paused phase
 * (re-plan or continue the build) with the decisions folded into its prompt.
 */
export function QuestionStage({
  task,
  info,
  canEdit,
}: {
  task: TaskDTO;
  info: PlanningInfoDTO;
  canEdit: boolean;
}) {
  const questions = useMemo(
    () => (info.questions ?? []).filter((q) => q.blocking !== false),
    [info.questions],
  );
  // For each question: the chosen option (or the OTHER sentinel) and the
  // free-text typed for an "Other" answer or an option-less question.
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [otherText, setOtherText] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const answer = useAnswerTaskPlanning(task.board_id, task.id);

  const answerFor = makeAnswerResolver(choice, otherText);
  const allAnswered = questions.every((q) => answerFor(q).trim().length > 0);

  if (questions.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-3.5 text-[13px] text-muted-foreground">
        The agent is waiting for answers, but no questions were found.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50/60 p-3.5 dark:border-amber-500/30 dark:bg-amber-500/5">
      <div className="mb-1 flex items-center gap-1.5">
        <HelpCircle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
        <span className="text-[13px] font-semibold text-foreground">
          The agent needs your input
        </span>
      </div>
      <p className="mb-3 text-[12px] text-muted-foreground">
        It paused instead of guessing. Answer the questions below to continue —
        your answers are passed straight to the agent.
      </p>

      <div className="space-y-3">
        {questions.map((q) => (
          <QuestionCard
            key={q.id}
            q={q}
            disabled={!canEdit}
            choice={choice[q.id]}
            otherText={otherText[q.id] ?? ""}
            onChoice={(v) => setChoice((s) => ({ ...s, [q.id]: v }))}
            onOther={(v) => setOtherText((s) => ({ ...s, [q.id]: v }))}
          />
        ))}
      </div>

      <div className="mt-3">
        <Field label="Note" hint="optional — anything beyond the questions">
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            disabled={!canEdit}
            placeholder="Extra context or constraints for the agent…"
            className="block w-full resize-y rounded border border-input bg-card px-2.5 py-1.5 text-[12.5px] text-foreground placeholder:text-muted-foreground/50 focus:border-primary focus:outline-none disabled:opacity-60"
          />
        </Field>
      </div>

      {canEdit && (
        <div className="mt-3 flex items-center gap-2">
          <Button
            size="sm"
            disabled={!allAnswered || answer.isPending}
            onClick={() => {
              const answers: Record<string, string> = {};
              for (const q of questions) answers[q.id] = answerFor(q).trim();
              answer.mutate(
                { answers, note: note.trim() || null },
                {
                  onSuccess: (r) =>
                    toast.success(
                      r.resumed === "execution"
                        ? "Answered — resuming the build"
                        : "Answered — re-planning",
                    ),
                  onError: (err) =>
                    toast.error(
                      err instanceof Error ? err.message : "Could not submit",
                    ),
                },
              );
            }}
          >
            {answer.isPending ? (
              <Spinner className="h-4 w-4" />
            ) : (
              <Send className="h-4 w-4" />
            )}
            Submit answers
          </Button>
          {!allAnswered && (
            <span className="text-[11.5px] text-muted-foreground">
              Answer every question to continue.
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** Resolve the effective answer string for a question from the form state. */
function makeAnswerResolver(
  choice: Record<string, string>,
  otherText: Record<string, string>,
) {
  return (q: PlanningQuestion): string => {
    const c = choice[q.id];
    // Option-less questions, or an explicit "Other", use the free-text input.
    if (!q.options || q.options.length === 0 || c === OTHER) {
      return otherText[q.id] ?? "";
    }
    return c ?? "";
  };
}

function QuestionCard({
  q,
  disabled,
  choice,
  otherText,
  onChoice,
  onOther,
}: {
  q: PlanningQuestion;
  disabled: boolean;
  choice: string | undefined;
  otherText: string;
  onChoice: (v: string) => void;
  onOther: (v: string) => void;
}) {
  const hasOptions = !!q.options && q.options.length > 0;
  const showOther = !hasOptions || choice === OTHER;
  return (
    <div className="rounded-md border border-border bg-card p-2.5">
      <p className="text-[12.5px] font-medium text-foreground">{q.question}</p>
      {q.reason && (
        <p className="mt-0.5 text-[11.5px] text-muted-foreground">{q.reason}</p>
      )}
      {hasOptions && (
        <div className="mt-2 space-y-1">
          {q.options!.map((opt) => (
            <label
              key={opt}
              className="flex items-center gap-2 text-[12.5px] text-foreground"
            >
              <input
                type="radio"
                name={`q-${q.id}`}
                checked={choice === opt}
                disabled={disabled}
                onChange={() => onChoice(opt)}
                className="h-3.5 w-3.5 accent-primary"
              />
              {opt}
            </label>
          ))}
          <label className="flex items-center gap-2 text-[12.5px] text-foreground">
            <input
              type="radio"
              name={`q-${q.id}`}
              checked={choice === OTHER}
              disabled={disabled}
              onChange={() => onChoice(OTHER)}
              className="h-3.5 w-3.5 accent-primary"
            />
            Other…
          </label>
        </div>
      )}
      {showOther && (
        <input
          type="text"
          value={otherText}
          disabled={disabled}
          onChange={(e) => onOther(e.target.value)}
          placeholder={hasOptions ? "Your answer…" : "Type your answer…"}
          className="mt-2 block w-full rounded border border-input bg-card px-2.5 py-1.5 text-[12.5px] text-foreground placeholder:text-muted-foreground/50 focus:border-primary focus:outline-none disabled:opacity-60"
        />
      )}
    </div>
  );
}

function ErrorNote({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-2 flex items-start gap-1.5 rounded border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-[12px] text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      {children}
    </div>
  );
}
