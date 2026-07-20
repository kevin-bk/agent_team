import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Activity,
  AlertTriangle,
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
  Maximize,
  Play,
  RotateCcw,
  X,
} from "@/components/icons";
import {
  useAckTaskLoop,
  useCancelTaskLoop,
  useGoalPublications,
  usePublishGoal,
  useResumeTaskLoop,
  useTaskChanges,
  useTaskGoalRun,
  useTaskGoalRuns,
  useTaskLoop,
  useTaskPlanning,
} from "@/api/hooks";
import type {
  AgentDTO,
  GoalRunReceiptDTO,
  LoopAttemptDTO,
  LoopInfoDTO,
  LoopState,
  LoopTaskDTO,
  LoopTaskStatus,
  TaskDTO,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ConfirmDialog";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { useBoardEventListener } from "../BoardEventsContext";
import { GoalStepper, type GoalStage } from "./GoalStepper";
import {
  LoopTimeline,
  useGoalActivity,
} from "./LoopTimeline";
import type { RoleKind } from "./roleSources";
import {
  PlannerDiscussion,
  PlanStage,
  QuestionStage,
  ReviewStage,
} from "./PlanningPanel";
import {
  ArchivedGoalSummary,
  DeliveryPanel,
  GoalActivityPanel,
  GoalHistoryBar,
  GoalPackageNav,
  HistoricalChangesPanel,
  LiveChangesPanel,
  PlanArchivePanel,
  VerificationPanel,
  historicalLoopInfo,
  type GoalView,
} from "./GoalPackage";
import {
  AgentLogo,
  AgentRoster,
  useAgentIndex,
  type LoopRole,
  type ResolvedAgent,
} from "./agentRoles";
import {
  LOOP_OUTCOME_LABEL,
  LOOP_STATE_META,
  LOOP_VERDICT_META,
} from "./loopStatus";

/** Maps a loop run role to its human label (the cockpit's vocabulary). */
const ROLE_LABELS: Record<string, string> = {
  planner: "Planner",
  reviewer: "Reviewer",
  generator: "Builder",
  evaluator: "Critic",
};

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
function stageFor(
  state: LoopState | null,
  restarting: boolean,
  approved: boolean,
): GoalStage {
  if (state === "planning" || state === "planning_paused") return "plan";
  if (state && REVIEW_STATES.includes(state)) return "review";
  if (state === "running") return "run";
  // A question pause sits in whichever phase raised it: planning (before the
  // plan is approved) shows under review, execution (after) shows under run.
  if (state === "waiting_answers") return approved ? "run" : "review";
  // Terminal or unset: a restart sends the user back to drafting a new plan.
  if (restarting || !state) return "plan";
  return "result";
}

function artifactJson(
  artifacts: { path: string; content?: string | null }[] | undefined,
  name: string,
): Record<string, unknown> {
  const raw = artifacts?.find(
    (artifact) => (artifact.path.split("/").pop() ?? "") === name,
  )?.content;
  if (!raw) return {};
  try {
    const value = JSON.parse(raw) as unknown;
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function receiptList(value: Record<string, unknown>): GoalRunReceiptDTO[] {
  const rows = Array.isArray(value.receipts) ? value.receipts : [];
  return rows.filter(
    (row): row is GoalRunReceiptDTO =>
      !!row && typeof row === "object" && typeof (row as GoalRunReceiptDTO).command === "string",
  );
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
  const changes = useTaskChanges(task.id);
  const goalRuns = useTaskGoalRuns(task.id);
  const [live, clearLive] = useLoopLiveStatus(task.id);
  const cancel = useCancelTaskLoop(task.id);
  const ack = useAckTaskLoop(task.board_id, task.id);
  const resume = useResumeTaskLoop(task.board_id, task.id);
  const [restarting, setRestarting] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [view, setView] = useState<GoalView>("summary");
  const [selectedGoalRun, setSelectedGoalRun] = useState<"live" | string>("live");
  const historicalGoal = useTaskGoalRun(
    task.id,
    selectedGoalRun === "live" ? undefined : selectedGoalRun,
  );

  const info = loop.data;
  const pinfo = planning.data;
  const runHistory = goalRuns.data ?? [];
  const currentGoalRun = runHistory.find(
    (item) => item.id === pinfo?.current_goal_run_id,
  );
  const historicalDetail = historicalGoal.data;
  const historical = selectedGoalRun !== "live";
  const publicationGoalId = historical
    ? selectedGoalRun
    : pinfo?.current_goal_run_id;
  const publications = useGoalPublications(
    task.id,
    publicationGoalId === "live" ? undefined : publicationGoalId ?? undefined,
  );
  const publishGoal = usePublishGoal(
    task.id,
    publicationGoalId && publicationGoalId !== "live" ? publicationGoalId : "_",
  );
  const confirm = useConfirm();
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

  const awaitingAnswers = state === "waiting_answers";
  const stage = stageFor(state, restarting, !!pinfo?.approved);
  const terminal = stage === "result" && !!state && state !== "running";
  const goalVerified = currentGoalRun?.outcome === "complete"
    && currentGoalRun?.verdict === "pass";
  const publicationReason = historical
    ? "Historical goals are read-only."
    : !currentGoalRun
      ? "Approve and run a goal before publishing."
      : !goalVerified
        ? "A completed goal with a trusted PASS verdict is required."
        : !canEdit
          ? "Editor access is required to publish."
          : undefined;

  const publishCurrentWorkspace = async () => {
    if (!currentGoalRun || publishGoal.isPending) return;
    const approved = await confirm({
      title: "Commit and create merge request?",
      description:
        "This will commit and push the current workspace changes, then create or retry the merge request. Continue?",
      confirmLabel: "Commit & create MR",
    });
    if (!approved) return;
    publishGoal.mutate(false, {
      onSuccess: (result) => {
        if (result.ok) {
          toast.success(result.detail || "Merge request created");
        } else {
          toast.error(result.detail || "Publication needs attention");
        }
      },
      onError: (error) => toast.error(
        error instanceof Error
          ? error.message
          : "Could not publish goal",
      ),
    });
  };

  useEffect(() => {
    setSelectedGoalRun("live");
    setView("summary");
  }, [task.id]);

  // Planning collaboration and approval controls belong to Plan & spec. Move
  // there when drafting starts, a human pause lands, or a draft reaches review;
  // later manual tab changes are preserved while the state stays unchanged.
  useEffect(() => {
    if (state === "planning" || state === "planning_paused" || stage === "review") {
      setView("plan");
    }
  }, [stage, state]);

  const livePlanArtifacts = useMemo(
    () =>
      (pinfo?.artifacts ?? []).filter((artifact) =>
        ["SPEC.md", "PLAN.md", "TASKS.json", "INTAKE.json", "PLAN_REVIEW.md"].includes(
          artifact.path.split("/").pop() ?? "",
        ),
      ),
    [pinfo?.artifacts],
  );
  const liveReceiptManifest = useMemo(
    () => artifactJson(pinfo?.artifacts, "VERIFICATION_RECEIPTS.json"),
    [pinfo?.artifacts],
  );
  const liveReceipts = useMemo(
    () => receiptList(liveReceiptManifest),
    [liveReceiptManifest],
  );
  const liveEvidence = useMemo(
    () => artifactJson(pinfo?.artifacts, "EVIDENCE.json"),
    [pinfo?.artifacts],
  );
  const historicalInfo = useMemo(
    () => (historicalDetail ? historicalLoopInfo(historicalDetail) : null),
    [historicalDetail],
  );

  // Which AI staffs each loop role, so the cockpit can name the planner /
  // builder / critic and highlight whoever is working right now. Hooks must run
  // before the early loading return, so they live up here.
  const resolveAgent = useAgentIndex(agents, cliAgents);
  const roles: LoopRole[] = useMemo(
    () => [
      {
        key: "planner",
        label: "Planner",
        agent: resolveAgent(info?.planner_agent_id),
      },
      {
        key: "generator",
        label: "Builder",
        agent: resolveAgent(info?.generator_agent_id),
      },
      {
        key: "evaluator",
        label: "Critic",
        agent: resolveAgent(info?.evaluator_agent_id),
      },
    ],
    [
      resolveAgent,
      info?.planner_agent_id,
      info?.generator_agent_id,
      info?.evaluator_agent_id,
    ],
  );
  const activityInfo = historical ? historicalInfo : info;
  const activityRoleAgents = useMemo(
    () => ({
      plan: resolveAgent(activityInfo?.planner_agent_id),
      build: resolveAgent(activityInfo?.generator_agent_id),
      critic: resolveAgent(activityInfo?.evaluator_agent_id),
    }),
    [
      resolveAgent,
      activityInfo?.planner_agent_id,
      activityInfo?.generator_agent_id,
      activityInfo?.evaluator_agent_id,
    ],
  );

  if (loop.isLoading && planning.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Spinner className="h-4 w-4" /> Loading goal…
      </div>
    );
  }

  const showTasks =
    !!info?.tasks && info.tasks.some((t) => t.status !== "pending");
  const showTimeline = attempts.length > 0;
  // The right-hand rail holds the (verbose) work transcript. It only earns its
  // column once a role has actually produced a conversation to show; until then
  // the flow stays a single centred column.
  const showActivity =
    !!info &&
    (!!info.planner_conversation_id ||
      !!info.reviewer_runs?.length ||
      !!info.generator_conversation_id ||
      info.attempts.some(
        (a) =>
          a.critic_conversation_id ||
          a.evaluations.some((e) => e.conversation_id),
      ));

  const activeTask = info?.tasks?.find((t) => t.status === "in_progress");

  const hasRoster = roles.some((r) => r.agent);
  const activeAgent = resolveAgent(info?.active_agent_id);
  const activeRoleLabel = info?.active_role
    ? (ROLE_LABELS[info.active_role] ?? null)
    : null;
  const selectedAttempts = historical
    ? (historicalDetail?.execution.attempts ?? [])
    : attempts;
  const selectedReceipts = historical
    ? (historicalDetail?.execution.receipts ?? [])
    : liveReceipts;
  const selectedEvidence = historical
    ? historicalDetail?.execution.evidence
    : liveEvidence;
  const counts: Partial<Record<GoalView, number>> = {
    plan: historical
      ? (historicalDetail?.artifact_count ?? 0)
      : livePlanArtifacts.filter((artifact) => artifact.exists).length,
    changes: historical
      ? (historicalDetail?.changed_file_count ?? 0)
      : (changes.data?.files.length ?? 0),
    verification: selectedReceipts.length,
    delivery: publications.data?.length ?? 0,
    activity: activityInfo
      ? Number(!!activityInfo.planner_conversation_id) +
        Number(!!activityInfo.generator_conversation_id) +
        activityInfo.attempts.filter((attempt) => !!attempt.critic_conversation_id).length
      : 0,
  };

  const stepView = (next: GoalStage) => {
    if (next === "plan" || next === "review") setView("plan");
    else if (next === "run") setView("activity");
    else setView("verification");
  };

  return (
    <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
      <div className="mx-auto w-full max-w-6xl px-4 py-4 lg:px-6">
        <GoalStepper
          current={historical ? "result" : stage}
          terminal={historical || terminal}
          onStepClick={stepView}
        />

        <GoalHistoryBar
          current={currentGoalRun}
          runs={runHistory}
          selected={selectedGoalRun}
          onChange={(id) => {
            setSelectedGoalRun(id);
            setView("summary");
          }}
          loading={goalRuns.isLoading}
        />

        {/* Slim status strip (state + iteration + tokens + Stop), merged into
            the header so it isn't a second boxed banner inside the flow. */}
        {!historical && !awaitingAnswers && state && (stage === "run" || stage === "result") && (
          <div className="mt-3">
            <StatusBanner
              state={state}
              live={live}
              info={info}
              canStop={running && canEdit}
              stopping={cancel.isPending}
              onStop={() =>
                cancel.mutate(undefined, {
                  onSuccess: (r) =>
                    r.ok
                      ? toast.success("Stopping after the current iteration")
                      : toast.message("No running goal to stop"),
                })
              }
            />
          </div>
        )}

        <GoalPackageNav value={view} onChange={setView} counts={counts} />

        <div className="mt-4 space-y-4">
          {historical && historicalGoal.isLoading && (
            <div className="flex min-h-72 items-center justify-center gap-2 text-[13px] text-muted-foreground">
              <Spinner className="h-4 w-4" /> Loading goal snapshot…
            </div>
          )}

          {view === "summary" && historicalDetail && (
            <ArchivedGoalSummary detail={historicalDetail} />
          )}

          {view === "summary" && !historical && (
            <>
              {awaitingAnswers && pinfo && (
                <QuestionStage task={task} info={pinfo} canEdit={canEdit} />
              )}

              {!awaitingAnswers && stage === "plan" && (
                <PlanStage
                  task={task}
                  agents={agents}
                  cliAgents={cliAgents}
                  canEdit={canEdit}
                  drafting={drafting}
                  paused={state === "planning_paused"}
                  lastError={pinfo?.last_error}
                  openImmediately={restarting}
                  onCancel={() => setRestarting(false)}
                />
              )}

              {!awaitingAnswers && stage === "result" && state && canEdit && (
                <ReviewActions
                  state={state}
                  missing={latestEval?.missing ?? ""}
                  attentionReason={info?.attention_reason ?? ""}
                  hasVerdict={latestEval !== null}
                  canResume={!!info?.can_resume}
                  agents={agents}
                  cliAgents={cliAgents}
                  builderAlias={info?.generator_agent_id}
                  criticAlias={info?.evaluator_agent_id}
                  resuming={resume.isPending}
                  onResume={(body) =>
                    resume.mutate(body, {
                      onSuccess: (r) =>
                        r.ok
                          ? toast.success("Resuming from where it stopped")
                          : toast.message("Could not resume"),
                      onError: (err) =>
                        toast.error(
                          err instanceof Error ? err.message : "Could not resume",
                        ),
                    })
                  }
                  onRunAgain={() => setRestarting(true)}
                  onAck={() => {
                    ack.mutate(undefined, {
                      onSuccess: () => clearLive(),
                      onError: (err) =>
                        toast.error(
                          err instanceof Error
                            ? err.message
                            : "Could not acknowledge",
                        ),
                    });
                  }}
                  acking={ack.isPending}
                />
              )}

              {hasRoster && (
                <AgentRoster roles={roles} activeRole={running ? info?.active_role : null} />
              )}

              {showActivity && (
                <LiveActivityCard
                  running={running}
                  currentTask={activeTask?.title ?? null}
                  activeAgent={running ? activeAgent : null}
                  activeRoleLabel={running ? activeRoleLabel : null}
                  onOpen={() => setActivityOpen(true)}
                />
              )}

              {(showTasks || showTimeline) && (
                <div className="grid gap-4 lg:grid-cols-2">
                  {showTasks && info?.tasks && (
                    <TaskGraphProgress tasks={info.tasks} />
                  )}
                  {showTimeline && <AttemptTimeline attempts={attempts} />}
                </div>
              )}

            </>
          )}

          {view === "plan" && !historical && awaitingAnswers && pinfo && (
            <QuestionStage task={task} info={pinfo} canEdit={canEdit} />
          )}

          {view === "plan" && !historical && !awaitingAnswers && stage === "review" && pinfo && (
            <ReviewStage
              task={task}
              agents={agents}
              cliAgents={cliAgents}
              canEdit={canEdit}
              info={pinfo}
            />
          )}

          {view === "plan" && !historical && !awaitingAnswers && stage === "plan" && (
            <PlanStage
              task={task}
              agents={agents}
              cliAgents={cliAgents}
              canEdit={canEdit}
              drafting={drafting}
              paused={state === "planning_paused"}
              lastError={pinfo?.last_error}
              openImmediately={restarting}
              onCancel={() => setRestarting(false)}
            />
          )}

          {view === "plan" && !historical && !awaitingAnswers && (
            drafting || state === "planning_paused"
          ) && (
            <PlannerDiscussion
              task={task}
              conversationId={info?.planner_conversation_id}
              drafting={drafting}
              paused={state === "planning_paused"}
              pauseRequested={!!pinfo?.pause_requested}
              canEdit={canEdit}
            />
          )}

          {view === "plan" && !historical && !awaitingAnswers && stage !== "plan" && stage !== "review" && (
            <PlanArchivePanel
              taskId={task.id}
              artifacts={livePlanArtifacts}
              approvedAt={pinfo?.approved_at}
              contractEtag={currentGoalRun?.contract_etag}
            />
          )}

          {view === "plan" && historicalDetail && (
            <PlanArchivePanel
              taskId={task.id}
              artifacts={historicalDetail.plan.artifacts ?? []}
              approvedAt={historicalDetail.approved_at}
              contractEtag={historicalDetail.contract_etag}
              title={`Approved plan package · Goal #${historicalDetail.run_no}`}
            />
          )}

          {view === "changes" && !historical && (
            <LiveChangesPanel
              taskId={task.id}
              data={changes.data}
              loading={changes.isLoading}
              error={changes.isError}
            />
          )}
          {view === "changes" && historicalDetail && (
            <HistoricalChangesPanel detail={historicalDetail} />
          )}

          {view === "verification" && (!historical || historicalDetail) && (
            <VerificationPanel
              attempts={selectedAttempts}
              receipts={selectedReceipts}
              evidence={selectedEvidence}
            />
          )}

          {view === "delivery" && (
            <DeliveryPanel
              publications={publications.data ?? []}
              canPublish={
                !historical
                && !!canEdit
                && goalVerified
                && !publications.isLoading
                && !publications.isError
              }
              reason={publicationReason}
              loading={publications.isLoading}
              loadError={publications.isError}
              publishing={!historical && publishGoal.isPending}
              onPublish={
                !historical && currentGoalRun
                  ? () => void publishCurrentWorkspace()
                  : undefined
              }
            />
          )}

          {view === "activity" && activityInfo && (
            <GoalActivityPanel
              taskId={task.id}
              info={activityInfo}
              running={!historical && running}
              roleAgents={activityRoleAgents}
            />
          )}
        </div>
      </div>

      {showActivity && info && (
        <ActivityPopup
          open={activityOpen}
          onClose={() => setActivityOpen(false)}
          taskId={task.id}
          info={info}
          running={running}
          roleAgents={{
            plan: roles[0].agent,
            build: roles[1].agent,
            critic: roles[2].agent,
          }}
        />
      )}
    </div>
  );
}

/**
 * A compact, always-visible card that hints at what the agent is doing right now
 * and opens the full transcript on demand. For a mostly-autonomous loop this is
 * enough day-to-day; the deep history is one click away.
 */
function LiveActivityCard({
  running,
  currentTask,
  activeAgent,
  activeRoleLabel,
  onOpen,
}: {
  running: boolean;
  currentTask: string | null;
  activeAgent?: ResolvedAgent | null;
  activeRoleLabel?: string | null;
  onOpen: () => void;
}) {
  // Headline names who's working: "Codex · Builder is working…" when known.
  const headline = running
    ? activeAgent
      ? `${activeAgent.name}${activeRoleLabel ? ` · ${activeRoleLabel}` : ""} is working…`
      : "Agent is working…"
    : "Work history";
  return (
    <div
      className={cn(
        "flex items-center gap-3.5 rounded-lg border px-4 py-3.5",
        running
          ? "border-emerald-300/60 bg-emerald-50 dark:border-emerald-500/25 dark:bg-emerald-500/10"
          : "border-border bg-card",
      )}
    >
      {running && activeAgent ? (
        <span className="relative shrink-0">
          <AgentLogo
            alias={activeAgent.alias}
            model={activeAgent.model}
            className="h-8 w-8"
            glyphClassName="h-4 w-4"
          />
          <span className="absolute -right-0.5 -top-0.5 flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-emerald-50 dark:ring-emerald-500/10" />
          </span>
        </span>
      ) : (
        <span className="relative flex h-3 w-3 shrink-0">
          <span className="relative inline-flex h-3 w-3 rounded-full bg-muted-foreground/40" />
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            "text-[13px] font-semibold",
            running
              ? "text-emerald-700 dark:text-emerald-300"
              : "text-foreground",
          )}
        >
          {headline}
        </div>
        <div className="truncate text-[12.5px] text-muted-foreground">
          {running
            ? (currentTask ?? "Running the next step…")
            : "Review the full plan, build and critic transcript."}
        </div>
      </div>
      <Button variant="outline" size="sm" className="shrink-0" onClick={onOpen}>
        View full activity
      </Button>
      <button
        type="button"
        onClick={onOpen}
        title="Open full activity"
        aria-label="Open full activity"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-card text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground"
      >
        <Maximize className="h-4 w-4" />
      </button>
    </div>
  );
}

function StatusBanner({
  state,
  live,
  info,
  canStop = false,
  onStop,
  stopping = false,
}: {
  state: LoopState;
  live: LiveStatus | null;
  info: { attempts: LoopAttemptDTO[]; objective?: string | null } | undefined;
  canStop?: boolean;
  onStop?: () => void;
  stopping?: boolean;
}) {
  const meta = LOOP_STATE_META[state];
  const Icon = meta.icon;
  const attempt = live?.attempt ?? info?.attempts.length ?? 0;
  const maxAttempts = live?.maxAttempts ?? 0;
  const tokens = live?.totalTokens ?? 0;
  return (
    <div
      className={cn(
        "flex items-center gap-2.5 rounded-lg border border-border px-3 py-2",
        meta.tone,
      )}
    >
      <Icon className={cn("h-4 w-4 shrink-0", meta.active && "animate-spin")} />
      <span className="shrink-0 text-[12.5px] font-semibold">
        Goal · {meta.label}
      </span>
      {info?.objective && (
        <span
          className="hidden min-w-0 flex-1 truncate text-[12px] opacity-70 sm:inline"
          title={info.objective}
        >
          {info.objective}
        </span>
      )}
      <div className="ml-auto flex shrink-0 items-center gap-3 text-[12px] font-medium tabular-nums">
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
        {canStop && onStop && (
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2"
            onClick={onStop}
            disabled={stopping}
          >
            <CircleSlash className="h-3.5 w-3.5" /> Stop
          </Button>
        )}
      </div>
    </div>
  );
}

function ReviewActions({
  state,
  missing,
  attentionReason,
  hasVerdict,
  onRunAgain,
  onAck,
  acking,
  canResume,
  agents,
  cliAgents,
  builderAlias,
  criticAlias,
  onResume,
  resuming,
}: {
  state: LoopState;
  missing: string;
  attentionReason: string;
  hasVerdict: boolean;
  onRunAgain: () => void;
  onAck: () => void;
  acking: boolean;
  canResume: boolean;
  agents: AgentDTO[];
  cliAgents: AgentDTO[];
  builderAlias?: string | null;
  criticAlias?: string | null;
  onResume: (body: { agent_id?: string; evaluator_id?: string }) => void;
  resuming: boolean;
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
              ? hasVerdict
                ? "Needs human review"
                : "Needs attention"
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
              {attentionReason ||
                "The goal stopped at a guardrail. Resume it from where it stopped, plan a new goal, or close."}
            </p>
          )}
        </div>
      </div>
      {canResume && (
        <ResumeControl
          agents={agents}
          cliAgents={cliAgents}
          builderAlias={builderAlias}
          criticAlias={criticAlias}
          onResume={onResume}
          resuming={resuming}
        />
      )}
      <div className="mt-3 flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onRunAgain}>
          <RotateCcw className="h-4 w-4" /> Plan a new goal
        </Button>
        <Button variant="ghost" size="sm" onClick={onAck} disabled={acking}>
          {needsHuman && !hasVerdict
            ? "Close without verification"
            : "Acknowledge & close"}
        </Button>
      </div>
    </div>
  );
}

/**
 * Resume the stopped loop from where it left off (skips finished tasks). The
 * builder/critic default to whoever ran last; "Change agents" reveals pickers so
 * a human can swap off a rate-limited engine for this resume.
 */
function ResumeControl({
  agents,
  cliAgents,
  builderAlias,
  criticAlias,
  onResume,
  resuming,
}: {
  agents: AgentDTO[];
  cliAgents: AgentDTO[];
  builderAlias?: string | null;
  criticAlias?: string | null;
  onResume: (body: { agent_id?: string; evaluator_id?: string }) => void;
  resuming: boolean;
}) {
  const [showOpts, setShowOpts] = useState(false);
  const options = useMemo(() => [...agents, ...cliAgents], [agents, cliAgents]);
  const [builder, setBuilder] = useState(builderAlias ?? "");
  const [critic, setCritic] = useState(criticAlias ?? "");

  const submit = () => {
    onResume({
      agent_id: builder || undefined,
      evaluator_id: critic || undefined,
    });
  };

  return (
    <div className="mt-3 rounded-md border border-emerald-300/50 bg-emerald-50/60 p-2.5 dark:border-emerald-500/25 dark:bg-emerald-500/10">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={submit} disabled={resuming}>
          {resuming ? (
            <Spinner className="h-4 w-4" />
          ) : (
            <Play className="h-4 w-4" />
          )}
          Resume from here
        </Button>
        <button
          type="button"
          onClick={() => setShowOpts((v) => !v)}
          className="text-[12px] font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        >
          {showOpts ? "Hide agents" : "Change agents"}
        </button>
        <span className="text-[11.5px] text-muted-foreground">
          Continues from the next unfinished task — finished work is kept.
        </span>
      </div>
      {showOpts && (
        <div className="mt-2.5 grid gap-2 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-[11.5px] font-medium text-muted-foreground">
            Builder
            <select
              value={builder}
              onChange={(e) => setBuilder(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1.5 text-[12.5px] text-foreground"
            >
              {options.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[11.5px] font-medium text-muted-foreground">
            Critic
            <select
              value={critic}
              onChange={(e) => setCritic(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1.5 text-[12.5px] text-foreground"
            >
              {options.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
    </div>
  );
}

const ROLE_FILTERS: { key: RoleKind | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "plan", label: "Planner" },
  { key: "review", label: "Plan reviews" },
  { key: "build", label: "Builder" },
  { key: "critic", label: "Critic" },
];

/**
 * A full-screen popup with the goal's merged work transcript — planning,
 * building and every critic iteration woven into one chronological timeline.
 * Lives behind a button so the day-to-day Goal view stays focused on progress;
 * the whole history is one click away.
 */
function ActivityPopup({
  open,
  onClose,
  taskId,
  info,
  running,
  roleAgents,
}: {
  open: boolean;
  onClose: () => void;
  taskId: string;
  info: LoopInfoDTO;
  running: boolean;
  roleAgents?: Partial<Record<RoleKind, ResolvedAgent | null>>;
}) {
  const { items, sources, streaming } = useGoalActivity(taskId, info, running);
  const [filter, setFilter] = useState<RoleKind | "all">("all");

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Only offer filters for the roles that actually produced work.
  const kinds = useMemo(() => new Set(sources.map((s) => s.kind)), [sources]);
  const shown = useMemo(
    () => (filter === "all" ? items : items.filter((i) => i.role.kind === filter)),
    [items, filter],
  );

  if (!open) return null;

  return (
    <>
      {/* Scrim */}
      <div
        className="fixed inset-0 z-50 bg-[#091e42]/50 animate-in fade-in-0 dark:bg-black/70"
        onClick={onClose}
      />
      {/* Panel */}
      <div className="fixed inset-3 z-50 flex flex-col overflow-hidden rounded-xl border border-border bg-background shadow-overlay sm:inset-6 lg:inset-10">
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <span className="text-[13px] font-semibold text-foreground">
            Activity — full history
          </span>
          {streaming && (
            <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600 dark:text-emerald-300">
              <Loader2 className="h-3 w-3 animate-spin" /> live
            </span>
          )}
          <div className="ml-auto flex items-center gap-1 rounded-md border border-border bg-surface-1 p-0.5">
            {ROLE_FILTERS.filter(
              (f) => f.key === "all" || kinds.has(f.key as RoleKind),
            ).map((f) => (
              <button
                key={f.key}
                type="button"
                onClick={() => setFilter(f.key)}
                className={cn(
                  "rounded px-2 py-0.5 text-[11.5px] font-medium transition-colors",
                  filter === f.key
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={onClose}
            title="Close (Esc)"
            aria-label="Close activity"
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
          <div className="mx-auto max-w-4xl px-2 py-2 sm:px-4">
            <LoopTimeline
              items={shown}
              streaming={streaming}
              roleAgents={roleAgents}
            />
          </div>
        </div>
      </div>
    </>
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
  const pct = tasks.length ? Math.round((done / tasks.length) * 100) : 0;
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="px-3 pb-2.5 pt-3">
        <div className="flex items-center gap-2 text-[13px] font-semibold text-foreground">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-emerald-500/15 text-emerald-600 dark:text-emerald-300">
            <ListChecks className="h-3.5 w-3.5" />
          </span>
          Tasks
          <span className="ml-auto font-normal tabular-nums text-muted-foreground">
            {done}/{tasks.length} done
          </span>
        </div>
        <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-surface-3">
          <div
            className="h-full rounded-full bg-emerald-500 transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
      <ul className="border-t border-border">
        {tasks.map((t) => {
          const meta = TASK_STATUS_META[t.status] ?? TASK_STATUS_META.pending;
          const Icon = meta.icon;
          return (
            <li
              key={t.id}
              className={cn(
                "flex items-center gap-2 border-b border-border/60 px-3 py-1.5 last:border-b-0",
                t.status === "in_progress" &&
                  "bg-sky-50 dark:bg-sky-500/10",
              )}
            >
              <Icon
                className={cn(
                  "h-4 w-4 shrink-0",
                  meta.cls,
                  meta.spin && "animate-spin",
                )}
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
  const ordered = [...attempts].reverse();
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-center gap-2 px-3 py-3 text-[13px] font-semibold text-foreground">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-violet-500/15 text-violet-600 dark:text-violet-300">
          <Gauge className="h-3.5 w-3.5" />
        </span>
        Iterations
        <span className="ml-auto font-normal tabular-nums text-muted-foreground">
          {attempts.length}
        </span>
      </div>
      <ul className="border-t border-border">
        {ordered.map((a, i) => (
          // Only the most recent iteration starts expanded; older ones collapse.
          <AttemptRow key={a.id} attempt={a} defaultOpen={i === 0} />
        ))}
      </ul>
    </div>
  );
}

function AttemptRow({
  attempt: a,
  defaultOpen,
}: {
  attempt: LoopAttemptDTO;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const verdict = a.evaluations[a.evaluations.length - 1];
  const vMeta = verdict ? LOOP_VERDICT_META[verdict.verdict] : null;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <li className="border-b border-border/60 last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-surface-1/60"
      >
        <Chevron className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
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
      </button>
      {open && verdict && (
        <div className="px-3 pb-2.5 pl-8">
          <VerdictDetail verdict={verdict} />
        </div>
      )}
    </li>
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
