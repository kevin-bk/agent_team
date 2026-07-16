import { useMemo, useState } from "react";
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Code2,
  ExternalLink,
  FileDiff,
  FileText,
  Gauge,
  GitBranch,
  History,
  ListChecks,
  Loader2,
  Send,
} from "@/components/icons";
import { DiffStatBadge, DiffView } from "@/components/DiffView";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type {
  GoalPublicationDTO,
  GoalRunDetailDTO,
  GoalRunReceiptDTO,
  GoalRunSummaryDTO,
  LoopAttemptDTO,
  LoopInfoDTO,
  LoopState,
  PlanningArtifactDTO,
  TaskChangesResponse,
} from "@/api/types";
import { cn } from "@/lib/utils";
import { ChangesView } from "./code/ChangesView";
import { ArtifactTabs } from "./PlanningPanel";
import { LoopTimeline, useGoalActivity, type RoleKind } from "./LoopTimeline";
import type { ResolvedAgent } from "./agentRoles";

export type GoalView =
  | "summary"
  | "plan"
  | "changes"
  | "verification"
  | "delivery"
  | "activity";

const VIEW_META: Array<{
  id: GoalView;
  label: string;
  icon: typeof Circle;
}> = [
  { id: "summary", label: "Summary", icon: Gauge },
  { id: "plan", label: "Plan & spec", icon: FileText },
  { id: "changes", label: "Changes", icon: Code2 },
  { id: "verification", label: "Verification", icon: CheckCircle2 },
  { id: "delivery", label: "Delivery", icon: Send },
  { id: "activity", label: "Activity", icon: Activity },
];

export function GoalPackageNav({
  value,
  onChange,
  counts = {},
}: {
  value: GoalView;
  onChange: (value: GoalView) => void;
  counts?: Partial<Record<GoalView, number>>;
}) {
  return (
    <nav className="mt-4 flex gap-1 overflow-x-auto rounded-lg border border-border bg-surface-1/60 p-1 scrollbar-thin">
      {VIEW_META.map((item) => {
        const Icon = item.icon;
        const active = value === item.id;
        const count = counts[item.id];
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onChange(item.id)}
            className={cn(
              "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[12px] font-medium transition-colors",
              active
                ? "bg-card text-foreground shadow-sm ring-1 ring-border/60"
                : "text-muted-foreground hover:bg-card/70 hover:text-foreground",
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {item.label}
            {typeof count === "number" && count > 0 && (
              <span
                className={cn(
                  "rounded-full px-1.5 text-[10px] tabular-nums",
                  active ? "bg-primary/10 text-primary" : "bg-surface-3",
                )}
              >
                {count}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}

function dateLabel(raw?: string | null) {
  if (!raw) return "Not started";
  const date = new Date(raw);
  return Number.isNaN(date.getTime())
    ? raw
    : new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
}

function statusTone(status: string) {
  if (status === "complete" || status === "pass") {
    return "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300";
  }
  if (status === "running" || status === "approved") {
    return "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300";
  }
  if (status === "failed" || status === "fail") {
    return "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300";
  }
  return "bg-surface-3 text-muted-foreground";
}

export function GoalHistoryBar({
  current,
  runs,
  selected,
  onChange,
  loading,
}: {
  current?: GoalRunSummaryDTO;
  runs: GoalRunSummaryDTO[];
  selected: "live" | string;
  onChange: (id: "live" | string) => void;
  loading: boolean;
}) {
  const historical = runs.filter((run) => run.id !== current?.id);
  const selectedRun = selected === "live" ? current : runs.find((r) => r.id === selected);
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-3 py-2.5">
      <span className="flex h-7 w-7 items-center justify-center rounded-md bg-violet-500/15 text-violet-600 dark:text-violet-300">
        <History className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        <p className="text-[11px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
          Goal package
        </p>
        <p className="truncate text-[12px] text-foreground">
          {selectedRun?.objective || "Current workspace"}
        </p>
      </div>
      <div className="ml-auto flex items-center gap-2">
        {selectedRun && (
          <span className={cn("rounded px-1.5 py-0.5 text-[10.5px] font-semibold uppercase", statusTone(selectedRun.status))}>
            {selectedRun.status.replaceAll("_", " ")}
          </span>
        )}
        <select
          aria-label="Select goal revision"
          value={selected}
          onChange={(e) => onChange(e.target.value)}
          disabled={loading}
          className="h-8 max-w-[250px] rounded-md border border-input bg-background px-2 text-[12px] font-medium text-foreground"
        >
          <option value="live">
            {current ? `Goal #${current.run_no} · Current` : "Current goal"}
          </option>
          {historical.map((run) => (
            <option key={run.id} value={run.id}>
              Goal #{run.run_no} · {run.status.replaceAll("_", " ")} · {dateLabel(run.approved_at)}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

export function PlanArchivePanel({
  taskId,
  artifacts,
  approvedAt,
  contractEtag,
  title = "Approved plan package",
}: {
  taskId: string;
  artifacts: PlanningArtifactDTO[];
  approvedAt?: string | null;
  contractEtag?: string | null;
  title?: string;
}) {
  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-indigo-500/15 text-indigo-600 dark:text-indigo-300">
          <ListChecks className="h-4 w-4" />
        </span>
        <div>
          <h3 className="text-[13.5px] font-semibold text-foreground">{title}</h3>
          <p className="text-[11.5px] text-muted-foreground">
            Read-only contract used by the builder and critic
            {approvedAt ? ` · approved ${dateLabel(approvedAt)}` : ""}
          </p>
        </div>
        {contractEtag && (
          <code className="ml-auto rounded bg-surface-2 px-2 py-1 text-[10.5px] text-muted-foreground" title={contractEtag}>
            {contractEtag.slice(0, 18)}…
          </code>
        )}
      </div>
      <ArtifactTabs taskId={taskId} artifacts={artifacts} canEdit={false} />
    </section>
  );
}

export function LiveChangesPanel({
  taskId,
  data,
  loading,
  error,
}: {
  taskId: string;
  data?: TaskChangesResponse;
  loading: boolean;
  error: boolean;
}) {
  const [filter, setFilter] = useState("");
  const [repo, setRepo] = useState("all");
  return (
    <section className="min-h-[480px] overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
        <FileDiff className="h-4 w-4 text-emerald-500" />
        <span className="text-[13px] font-semibold text-foreground">Current workspace changes</span>
        <span className="text-[11.5px] text-muted-foreground">Git truth versus each repo base branch</span>
        <div className="ml-auto flex items-center gap-2">
          {(data?.repos.length ?? 0) > 1 && (
            <select
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              className="h-8 rounded-md border border-input bg-background px-2 text-[12px]"
            >
              <option value="all">All repositories</option>
              {data?.repos.map((item) => (
                <option key={item.slug} value={item.slug}>{item.slug}</option>
              ))}
            </select>
          )}
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter files…"
            className="h-8 w-40 text-[12px]"
          />
        </div>
      </div>
      <ChangesView
        taskId={taskId}
        data={data}
        isLoading={loading}
        isError={error}
        filter={filter}
        repoFilter={repo}
      />
    </section>
  );
}

export function HistoricalChangesPanel({ detail }: { detail: GoalRunDetailDTO }) {
  const changes = detail.workspace.changes;
  const diffs = detail.workspace.diffs ?? {};
  const [open, setOpen] = useState<string | null>(null);
  if (!changes?.files?.length) {
    return <EmptyPanel icon={FileDiff} title="No captured changes" body="This goal revision did not leave a workspace diff, or it predates immutable workspace snapshots." />;
  }
  const added = changes.files.reduce((sum, file) => sum + file.additions, 0);
  const removed = changes.files.reduce((sum, file) => sum + file.deletions, 0);
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center gap-2 border-b border-border px-3 py-3">
        <FileDiff className="h-4 w-4 text-emerald-500" />
        <span className="text-[13px] font-semibold">Snapshot for Goal #{detail.run_no}</span>
        <span className="text-[11.5px] text-muted-foreground">{changes.files.length} files</span>
        <DiffStatBadge added={added} removed={removed} />
      </div>
      <div className="divide-y divide-border">
        {changes.files.map((file) => {
          const key = `${file.repo}:${file.path}`;
          const diff = diffs[key];
          const expanded = open === key;
          return (
            <div key={key}>
              <button
                type="button"
                onClick={() => setOpen(expanded ? null : key)}
                className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-surface-1"
              >
                {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                <span className="rounded bg-surface-3 px-1.5 font-mono text-[10px]">{file.status}</span>
                <code className="min-w-0 flex-1 truncate text-[11.5px]">{file.repo}/{file.path}</code>
                <DiffStatBadge added={file.additions} removed={file.deletions} />
              </button>
              {expanded && (
                <div className="border-t border-border bg-surface-1/40 p-2">
                  {diff ? (
                    diff.binary ? (
                      <p className="p-4 text-center text-[12px] text-muted-foreground">Binary file</p>
                    ) : (
                      <DiffView oldText={diff.original} newText={diff.modified} maxHeightClass="max-h-[520px]" />
                    )
                  ) : (
                    <p className="p-4 text-center text-[12px] text-muted-foreground">Diff content was not retained in the snapshot.</p>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function ArchivedGoalSummary({ detail }: { detail: GoalRunDetailDTO }) {
  const tasks = detail.execution.final_tasks?.tasks ?? [];
  const done = tasks.filter((task) => task.status === "complete" || task.status === "skipped").length;
  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-start gap-3">
          <span className={cn("mt-0.5 flex h-8 w-8 items-center justify-center rounded-lg", statusTone(detail.status))}>
            <History className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-[14px] font-semibold">Goal #{detail.run_no}</h3>
              <span className={cn("rounded px-1.5 py-0.5 text-[10.5px] font-semibold uppercase", statusTone(detail.status))}>{detail.status}</span>
              {detail.verdict && <span className={cn("rounded px-1.5 py-0.5 text-[10.5px] font-semibold uppercase", statusTone(detail.verdict))}>Critic: {detail.verdict}</span>}
            </div>
            <p className="mt-1 text-[13px] text-foreground">{detail.objective || "No objective recorded"}</p>
            <p className="mt-1 text-[11.5px] text-muted-foreground">Approved {dateLabel(detail.approved_at)} · completed {dateLabel(detail.completed_at)}</p>
          </div>
        </div>
      </section>
      <div className="grid gap-3 sm:grid-cols-4">
        <Metric label="Plan files" value={detail.artifact_count} />
        <Metric label="Changed files" value={detail.changed_file_count} />
        <Metric label="Trusted commands" value={detail.receipt_count} />
        <Metric label="Tasks done" value={tasks.length ? `${done}/${tasks.length}` : "—"} />
      </div>
    </div>
  );
}

export function DeliveryPanel({
  publications,
  canPublish,
  reason,
  loading = false,
  loadError = false,
  publishing,
  onPublish,
}: {
  publications: GoalPublicationDTO[];
  canPublish: boolean;
  reason?: string;
  loading?: boolean;
  loadError?: boolean;
  publishing: boolean;
  onPublish?: () => void;
}) {
  const hasErrors = publications.some((publication) => publication.status === "error");
  const complete = publications.length > 0 && publications.every(
    (publication) => publication.status === "published",
  );
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-500/15 text-blue-600 dark:text-blue-300">
          <Send className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[13.5px] font-semibold text-foreground">Delivery</h3>
          <p className="text-[11.5px] text-muted-foreground">
            Human-confirmed commit, push, and merge request creation from the current workspace
          </p>
        </div>
        {onPublish && !complete && (
          <Button
            size="sm"
            disabled={!canPublish || publishing}
            onClick={onPublish}
            title={!canPublish ? reason : undefined}
          >
            {publishing ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-1.5 h-3.5 w-3.5" />}
            {hasErrors ? "Retry publication" : "Commit & create MR"}
          </Button>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-2 px-4 py-8 text-[12px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading delivery status…
        </div>
      ) : loadError ? (
        <p className="bg-rose-50 px-4 py-5 text-[12px] text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          Delivery status could not be loaded. Refresh the page before publishing.
        </p>
      ) : publications.length ? (
        <div className="divide-y divide-border">
          {publications.map((publication) => (
            <div key={publication.id} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-[12.5px] font-semibold text-foreground">{publication.repo_slug}</span>
                <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase", statusTone(publication.status))}>
                  {publication.status}
                </span>
                {publication.pushed && (
                  <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
                    pushed
                  </span>
                )}
                {publication.request_url && (
                  <a
                    href={publication.request_url}
                    target="_blank"
                    rel="noreferrer"
                    className="ml-auto inline-flex items-center gap-1 text-[11.5px] font-semibold text-primary hover:underline"
                  >
                    {publication.provider === "github" ? "PR" : "MR"} #{publication.request_number}
                    <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>
              <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[10.5px] text-muted-foreground">
                <span>{publication.source_branch} → {publication.target_branch}</span>
                {publication.commit_sha && <code>commit {publication.commit_sha.slice(0, 10)}</code>}
                <code title={publication.tree_sha}>approved tree {publication.tree_sha.slice(0, 10)}</code>
              </div>
              {publication.error && (
                <p className="mt-2 rounded-md bg-rose-50 px-2.5 py-2 text-[11.5px] text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
                  {publication.error}
                </p>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="px-4 py-5 text-[12px] text-muted-foreground">
          {reason || "No delivery record yet. Verification must pass before publication."}
        </p>
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-3">
      <p className="text-[10.5px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">{label}</p>
      <p className="mt-1 text-[18px] font-semibold tabular-nums text-foreground">{value}</p>
    </div>
  );
}

export function VerificationPanel({
  attempts,
  receipts,
  evidence,
}: {
  attempts: LoopAttemptDTO[];
  receipts: GoalRunReceiptDTO[];
  evidence?: Record<string, unknown>;
}) {
  const evaluations = attempts.flatMap((attempt) => attempt.evaluations ?? []);
  const passed = receipts.filter((receipt) => receipt.exit_code === 0 && !receipt.timed_out).length;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Metric label="Iterations" value={attempts.length} />
        <Metric label="Command receipts" value={receipts.length ? `${passed}/${receipts.length}` : "—"} />
        <Metric label="Critic verdicts" value={evaluations.length} />
      </div>
      <section className="overflow-hidden rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 border-b border-border px-3 py-3">
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          <span className="text-[13px] font-semibold">Trusted command receipts</span>
        </div>
        {receipts.length ? (
          <div className="divide-y divide-border">
            {receipts.map((receipt) => {
              const pass = receipt.exit_code === 0 && !receipt.timed_out;
              return (
                <div key={receipt.id} className="flex items-start gap-2.5 px-3 py-2.5">
                  {pass ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-500" /> : <Circle className="mt-0.5 h-4 w-4 text-rose-500" />}
                  <div className="min-w-0 flex-1">
                    <code className="block break-all text-[11.5px] text-foreground">{receipt.command}</code>
                    <p className="mt-0.5 text-[10.5px] text-muted-foreground">{receipt.repo || receipt.working_directory} · {receipt.duration_ms.toLocaleString()} ms · exit {receipt.exit_code}</p>
                  </div>
                  <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase", statusTone(pass ? "pass" : "fail"))}>{pass ? "pass" : "fail"}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="px-4 py-8 text-center text-[12px] text-muted-foreground">No backend command receipts were captured for this goal.</p>
        )}
      </section>
      {evaluations.length > 0 && (
        <section className="overflow-hidden rounded-lg border border-border bg-card">
          <div className="border-b border-border px-3 py-3 text-[13px] font-semibold">Critic decisions</div>
          <div className="divide-y divide-border">
            {evaluations.map((evaluation) => (
              <div key={evaluation.id} className="flex items-start gap-3 px-3 py-2.5">
                <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase", statusTone(evaluation.verdict))}>{evaluation.verdict}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] font-medium">Score {(evaluation.score * 100).toFixed(0)}%</p>
                  {evaluation.missing && <p className="mt-0.5 whitespace-pre-wrap text-[11.5px] text-muted-foreground">{evaluation.missing}</p>}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
      {evidence && Object.keys(evidence).length > 0 && (
        <details className="rounded-lg border border-border bg-card">
          <summary className="cursor-pointer px-3 py-3 text-[12px] font-semibold">Raw evaluator evidence</summary>
          <pre className="max-h-96 overflow-auto border-t border-border p-3 text-[10.5px] text-muted-foreground">{JSON.stringify(evidence, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

const ROLE_FILTERS: { key: RoleKind | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "plan", label: "Planner" },
  { key: "build", label: "Builder" },
  { key: "critic", label: "Critic" },
];

export function GoalActivityPanel({
  taskId,
  info,
  running,
  roleAgents,
}: {
  taskId: string;
  info: LoopInfoDTO;
  running: boolean;
  roleAgents?: Partial<Record<RoleKind, ResolvedAgent | null>>;
}) {
  const { items, sources, streaming } = useGoalActivity(taskId, info, running);
  const [filter, setFilter] = useState<RoleKind | "all">("all");
  const kinds = useMemo(() => new Set(sources.map((source) => source.kind)), [sources]);
  const shown = filter === "all" ? items : items.filter((item) => item.role.kind === filter);
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
        <Activity className="h-4 w-4 text-sky-500" />
        <span className="text-[13px] font-semibold">Goal activity</span>
        {streaming && <span className="inline-flex items-center gap-1 text-[10.5px] font-medium text-emerald-600"><Loader2 className="h-3 w-3 animate-spin" /> live</span>}
        <div className="ml-auto flex items-center gap-1 rounded-md border border-border bg-surface-1 p-0.5">
          {ROLE_FILTERS.filter((item) => item.key === "all" || kinds.has(item.key as RoleKind)).map((item) => (
            <button key={item.key} type="button" onClick={() => setFilter(item.key)} className={cn("rounded px-2 py-0.5 text-[11px] font-medium", filter === item.key ? "bg-card text-foreground shadow-sm" : "text-muted-foreground")}>{item.label}</button>
          ))}
        </div>
      </div>
      <div className="max-h-[680px] overflow-auto px-3 py-2 scrollbar-thin">
        {sources.length ? <LoopTimeline items={shown} streaming={streaming} roleAgents={roleAgents} /> : <div className="flex min-h-56 items-center justify-center text-[12px] text-muted-foreground">No agent transcript was captured for this goal.</div>}
      </div>
    </section>
  );
}

export function historicalLoopInfo(detail: GoalRunDetailDTO): LoopInfoDTO {
  const roles = detail.execution.roles ?? [];
  const planner = roles.find((role) => role.role === "planner");
  const generator = [...roles].reverse().find((role) => role.role === "generator");
  const attempts = (detail.execution.attempts ?? []).map((attempt) => {
    const critic = [...roles].reverse().find((role) => role.role === "evaluator" && role.attempt_id === attempt.id);
    const build = [...roles].reverse().find((role) => role.role === "generator" && role.attempt_id === attempt.id);
    return {
      ...attempt,
      run_id: build?.run_id ?? attempt.run_id,
      conversation_id: build?.conversation_id ?? attempt.conversation_id,
      critic_run_id: critic?.run_id ?? attempt.critic_run_id,
      critic_conversation_id: critic?.conversation_id ?? attempt.critic_conversation_id,
      evaluations: (attempt.evaluations ?? []).map((evaluation) => ({
        ...evaluation,
        conversation_id: critic?.conversation_id ?? evaluation.conversation_id,
      })),
    };
  });
  return {
    task_id: detail.task_id,
    execution_mode: "autonomous",
    loop_state: (detail.outcome as LoopState | null) ?? null,
    objective: detail.objective,
    is_running: false,
    planner_conversation_id: planner?.conversation_id,
    planner_run_id: planner?.run_id,
    planner_agent_id: planner?.agent_id,
    generator_conversation_id: generator?.conversation_id,
    generator_agent_id: generator?.agent_id,
    evaluator_agent_id: [...roles].reverse().find((role) => role.role === "evaluator")?.agent_id,
    attempts,
    tasks: detail.execution.final_tasks?.tasks ?? [],
  };
}

function EmptyPanel({
  icon: Icon,
  title,
  body,
}: {
  icon: typeof Circle;
  title: string;
  body: string;
}) {
  return (
    <div className="flex min-h-72 flex-col items-center justify-center rounded-lg border border-dashed border-border bg-card/50 px-8 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface-3 text-muted-foreground"><Icon className="h-5 w-5" /></span>
      <p className="mt-3 text-[13px] font-semibold">{title}</p>
      <p className="mt-1 max-w-md text-[12px] text-muted-foreground">{body}</p>
    </div>
  );
}
