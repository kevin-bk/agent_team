import {
  AlignLeft,
  Archive,
  ArrowLeft,
  ArrowRight,
  Bot,
  Check,
  ChevronDown,
  CircleDot,
  CircleSlash,
  ExternalLink,
  Eye,
  EyeOff,
  FileDiff,
  FileText,
  FolderGit2,
  History,
  MessagesSquare,
  PanelRightClose,
  PanelRightOpen,
  Paperclip,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Sparkles,
  UserRound,
  X,
} from "@/components/icons";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useApi } from "@/api/ApiProvider";
import {
  useAgents,
  useBoard,
  useBoardMembers,
  useCreateTaskComment,
  useDeleteTaskComment,
  useMe,
  usePatchTask,
  useResetTaskThread,
  useSyncTaskFromJira,
  useTaskActivity,
  useTaskAttemptMessages,
  useTaskAttempts,
  useTaskComments,
  useTaskFileBlobUrl,
  useTaskRuns,
  useUpdateTaskComment,
} from "@/api/hooks";
import type {
  AttemptDTO,
  BoardColumn,
  CommentAttachment,
  TaskActivityDTO,
  TaskCommentDTO,
  TaskDTO,
} from "@/api/types";
import {
  AttachmentChips,
  usePendingAttachments,
} from "@/components/attachments";
import {
  IssueTypeIcon,
  JiraAvatar,
  taskIssueType,
  ISSUE_TYPE_META,
  ISSUE_TYPE_ORDER,
} from "@/components/jira";
import { useConfirm } from "@/components/ConfirmDialog";
import { UserSelect } from "@/components/UserSelect";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { blocksFromHistory } from "@/features/chat/reducer";
import { Markdown } from "@/components/Markdown";
import { Timeline } from "@/features/chat/Timeline";
import { cn } from "@/lib/utils";
import { useTaskAgentRun } from "./cockpit/useTaskAgentRun";
import { useTypingIndicator } from "./cockpit/useTypingIndicator";
import { FileViewerModal } from "./cockpit/FileViewerModal";
import { NoteEditor } from "./cockpit/NoteEditor";
import { RunChanges, changedFileCount } from "./cockpit/RunChanges";
import { ARTIFACT_DND_TYPE, TaskFiles } from "./cockpit/TaskFiles";
import { TaskRepoCard } from "./cockpit/TaskRepoCard";
import { PRIORITY_META, PRIORITY_ORDER, PriorityIcon } from "./priority";
import { SelectMenu } from "@/components/ui/select-menu";
import { statusColor } from "./statusColor";

const OVERVIEW = "__overview__";

/**
 * The issue-type glyph in the cockpit header. Read-only for viewers; editors
 * get a compact dropdown that persists the new type via PATCH.
 */
function TaskTypeControl({
  task,
  canEdit,
}: {
  task: TaskDTO;
  canEdit: boolean;
}) {
  const patch = usePatchTask(task.board_id);
  const current = taskIssueType(task);

  if (!canEdit) return <IssueTypeIcon type={current} size={16} />;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title={`Type: ${ISSUE_TYPE_META[current].label}`}
          className="inline-flex items-center gap-0.5 rounded p-0.5 transition-colors hover:bg-surface-1"
        >
          <IssueTypeIcon type={current} size={16} />
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-44">
        {ISSUE_TYPE_ORDER.map((t) => (
          <DropdownMenuItem
            key={t}
            onSelect={() => {
              if (t !== current)
                patch.mutate({ taskId: task.id, body: { task_type: t } });
            }}
            className={cn(
              "gap-2",
              t === current &&
                "bg-primary/10 text-primary focus:bg-primary/10 focus:text-primary",
            )}
          >
            <IssueTypeIcon type={t} size={16} />
            <span className="min-w-0 flex-1 truncate">
              {ISSUE_TYPE_META[t].label}
            </span>
            {t === current && (
              <Check className="h-4 w-4 shrink-0 text-primary" />
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function TaskCockpit({
  task,
  canEdit,
  onBack,
  onEdit,
}: {
  task: TaskDTO;
  canEdit: boolean;
  onBack: () => void;
  onEdit: () => void;
}) {
  const agents = useAgents();
  const board = useBoard(task.board_id);
  const mentionable = useMemo(() => {
    // Only agents explicitly staffed on the board (Board settings → Agents)
    // appear here; an unstaffed board shows none. Guard on `board.data` so we
    // don't flash "no agents" while the board is still loading.
    if (!board.data) return [];
    const staffed = board.data.agent_ids ?? [];
    return (agents.data ?? []).filter(
      (a) => a.enabled && a.mentionable && staffed.includes(a.id),
    );
  }, [agents.data, board.data]);
  const runs = useTaskRuns(task.id);
  const runningAgents = useMemo(() => {
    const set = new Set<string>();
    for (const r of runs.data ?? []) {
      if (r.status === "running" || r.status === "queued") set.add(r.agent_id);
    }
    return set;
  }, [runs.data]);

  const [thread, setThread] = useState<string>(OVERVIEW);
  const [filePath, setFilePath] = useState("");
  const [artifactsOpen, setArtifactsOpen] = useState(true);
  // When viewing a *past* attempt (read-only history); null = the live thread.
  const [viewConvId, setViewConvId] = useState<string | null>(null);
  // Bumped on reset to remount the live Conversation so it reloads (empty).
  const [resetNonce, setResetNonce] = useState(0);
  const reset = useResetTaskThread(task.id);
  const syncJira = useSyncTaskFromJira(task.board_id);
  const [keyPromptOpen, setKeyPromptOpen] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const confirm = useConfirm();
  const qc = useQueryClient();
  const refreshArtifacts = () =>
    qc.invalidateQueries({ queryKey: ["task-file-tree", task.id] });

  const activeAgent =
    thread === OVERVIEW ? null : mentionable.find((a) => a.id === thread);

  const selectThread = (id: string) => {
    setThread(id);
    setViewConvId(null);
  };

  const attempts = useTaskAttempts(task.id, activeAgent?.id);
  const attemptList = attempts.data ?? [];
  const activeAttempt = attemptList.find((a) => a.is_active);
  // Are we currently viewing the live thread (vs. an archived attempt)?
  const viewingLive =
    viewConvId === null || viewConvId === activeAttempt?.conv_id;

  const resetThread = async () => {
    if (!activeAgent || reset.isPending) return;
    const ok = await confirm({
      title: "Reset this conversation?",
      description:
        "The shared workspace files are kept — the agent just starts a fresh thread. The current conversation is archived and stays available in History.",
      confirmLabel: "Reset",
      tone: "danger",
    });
    if (!ok) return;
    await reset.mutateAsync(activeAgent.id);
    setViewConvId(null);
    setResetNonce((n) => n + 1);
  };

  const syncWithKey = async (jiraKey?: string) => {
    if (syncJira.isPending) return;
    try {
      await syncJira.mutateAsync({ taskId: task.id, jiraKey });
      toast.success("Synced from Jira");
      setKeyPromptOpen(false);
      setKeyInput("");
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to sync from Jira",
      );
    }
  };

  // No linked key yet → ask for one inline before syncing.
  const onSyncClick = () =>
    task.jira_key ? void syncWithKey() : setKeyPromptOpen(true);

  return (
    <div className="font-ui flex h-full flex-col bg-background">
      {/* Header — Jira issue view: small breadcrumb row, then a big title. */}
      <header className="border-b border-border bg-card px-5 pb-3 pt-2.5">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" onClick={onBack} aria-label="Back">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <TaskTypeControl task={task} canEdit={canEdit} />
          <span className="shrink-0 text-[13px] font-medium uppercase text-muted-foreground">
            {task.human_key}
          </span>
          {task.jira_key &&
            (task.jira_url ? (
              <a
                href={task.jira_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-[13px] font-medium uppercase text-muted-foreground transition-colors hover:text-primary"
              >
                / {task.jira_key}
                <ExternalLink className="h-3 w-3" />
              </a>
            ) : (
              <span className="text-[13px] font-medium uppercase text-muted-foreground">
                / {task.jira_key}
              </span>
            ))}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <span
              className="hidden max-w-[14rem] items-center gap-1 truncate font-mono text-[11px] text-muted-foreground lg:inline-flex"
              title={task.workspace_path}
            >
              <FolderGit2 className="h-3 w-3 shrink-0" />
              <span className="truncate">{task.workspace_path}</span>
            </span>
            {canEdit && board.data?.jira_enabled && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onSyncClick}
                disabled={syncJira.isPending}
                title={
                  task.jira_key
                    ? `Pull ${task.jira_key} from Jira`
                    : "Link a Jira key, then sync"
                }
              >
                <RefreshCw
                  className={cn(
                    "h-3.5 w-3.5",
                    syncJira.isPending && "animate-spin",
                  )}
                />{" "}
                Sync
              </Button>
            )}
            {canEdit && (
              <Button variant="ghost" size="sm" onClick={onEdit}>
                <Pencil className="h-3.5 w-3.5" /> Edit
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              aria-label={artifactsOpen ? "Hide details" : "Show details"}
              title={artifactsOpen ? "Hide details" : "Show details"}
              onClick={() => setArtifactsOpen((v) => !v)}
            >
              {artifactsOpen ? (
                <PanelRightClose className="h-4 w-4" />
              ) : (
                <PanelRightOpen className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
        <h1
          className="mt-1 truncate pl-10 text-[20px] font-semibold leading-snug text-foreground"
          title={task.title}
        >
          {task.title}
        </h1>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Left — thread list (Overview + per-agent), Jira project-nav style */}
        <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-surface-1/60">
          <div className="px-4 pb-1.5 pt-4 text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            Threads
          </div>
          <div className="min-h-0 flex-1 space-y-0.5 overflow-auto px-2 pb-2 scrollbar-thin">
            <ThreadItem
              icon={
                <span className="flex h-6 w-6 items-center justify-center rounded bg-primary/10 text-primary">
                  <MessagesSquare className="h-3.5 w-3.5" />
                </span>
              }
              label="Overview"
              sub="Notes & discussion"
              active={thread === OVERVIEW}
              onClick={() => selectThread(OVERVIEW)}
            />

            <div className="px-2 pb-1 pt-3 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
              Agents
            </div>
            {agents.isLoading ? (
              <div className="flex items-center gap-1.5 px-2 py-2 text-xs text-muted-foreground">
                <Spinner className="h-3 w-3" /> loading…
              </div>
            ) : mentionable.length === 0 ? (
              <div className="px-2 py-2 text-xs text-muted-foreground">
                No agents on this board. Assign some in Board settings.
              </div>
            ) : (
              mentionable.map((a) => {
                const c = statusColor(a.id);
                return (
                  <ThreadItem
                    key={a.id}
                    icon={
                      <span
                        className={cn(
                          "relative flex h-6 w-6 items-center justify-center rounded-md",
                          c.soft,
                        )}
                      >
                        <Bot className="h-3.5 w-3.5" />
                        {runningAgents.has(a.id) && (
                          <span className="absolute -right-0.5 -top-0.5 h-2 w-2 animate-pulse rounded-full bg-emerald-500 ring-2 ring-white dark:ring-surface-1" />
                        )}
                      </span>
                    }
                    label={a.display_name}
                    sub={runningAgents.has(a.id) ? "running…" : a.model ?? "agent"}
                    active={thread === a.id}
                    onClick={() => selectThread(a.id)}
                  />
                );
              })
            )}
          </div>
        </aside>

        {/* Middle — selected thread */}
        <section className="flex min-w-0 flex-1 flex-col">
          {thread === OVERVIEW ? (
            <OverviewThread
              task={task}
              canComment={canEdit}
              onViewFile={setFilePath}
            />
          ) : activeAgent ? (
            <>
              <div className="flex items-center gap-2 border-b border-border px-4 py-2">
                <span
                  className={cn(
                    "flex h-6 w-6 items-center justify-center rounded",
                    statusColor(activeAgent.id).soft,
                  )}
                >
                  <Bot className="h-3.5 w-3.5" />
                </span>
                <span className="text-sm font-semibold text-foreground">
                  {activeAgent.display_name}
                </span>
                {activeAgent.model && (
                  <span className="rounded-sm bg-surface-1 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                    {activeAgent.model}
                  </span>
                )}
                <div className="ml-auto flex items-center gap-1">
                  {attemptList.length > 1 && (
                    <AttemptHistoryMenu
                      attempts={attemptList}
                      activeConvId={activeAttempt?.conv_id}
                      viewConvId={viewConvId}
                      onSelect={(a) =>
                        setViewConvId(a.is_active ? null : a.conv_id)
                      }
                    />
                  )}
                  <button
                    type="button"
                    onClick={() => void resetThread()}
                    disabled={reset.isPending}
                    className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-surface-1 hover:text-foreground disabled:opacity-50"
                    title="Reset conversation"
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> Reset
                  </button>
                </div>
              </div>
              {viewingLive ? (
                <Conversation
                  key={`${task.id}:${activeAgent.id}:${activeAttempt?.conv_id ?? "live"}:${resetNonce}`}
                  taskId={task.id}
                  agentId={activeAgent.id}
                  agentName={activeAgent.display_name}
                  canEdit={canEdit}
                  workspaceRoot={task.workspace_path}
                  onOpenFile={setFilePath}
                />
              ) : (
                <AttemptHistoryView
                  key={`${task.id}:${activeAgent.id}:${viewConvId}`}
                  taskId={task.id}
                  agentId={activeAgent.id}
                  convId={viewConvId as string}
                  attempt={
                    attemptList.find((a) => a.conv_id === viewConvId)?.attempt
                  }
                  onOpenFile={setFilePath}
                  onReturnToLive={() => setViewConvId(null)}
                />
              )}
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
              Select a thread.
            </div>
          )}
        </section>

        {/* Right — Details (status / people) + Artifacts (collapsible) */}
        {artifactsOpen && (
          <aside className="flex w-96 shrink-0 flex-col border-l border-border">
            <TaskDetailsPanel
              task={task}
              canEdit={canEdit}
              onClose={() => setArtifactsOpen(false)}
            />
            <TaskRepoCard
              taskId={task.id}
              canEdit={canEdit}
              onOpenPath={setFilePath}
            />
            <div className="flex items-center gap-2 border-y border-border px-4 py-2.5">
              <FolderGit2 className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-[13px] font-semibold text-foreground">
                Artifacts
              </span>
              <span className="text-[11px] text-muted-foreground">· workspace</span>
              <button
                type="button"
                onClick={refreshArtifacts}
                aria-label="Refresh files"
                title="Refresh files"
                className="ml-auto rounded p-1 text-muted-foreground transition-colors hover:bg-surface-1 hover:text-foreground"
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
            </div>
            <TaskFiles
              taskId={task.id}
              selected={filePath}
              onSelect={setFilePath}
              canDelete={canEdit}
              onDeleted={(p) => {
                // Close the viewer if the open file (or its folder) was deleted.
                if (filePath === p || filePath.startsWith(`${p}/`)) setFilePath("");
              }}
            />
          </aside>
        )}
      </div>
      {filePath && (
        <FileViewerModal
          taskId={task.id}
          path={filePath}
          canEdit={canEdit}
          onClose={() => setFilePath("")}
          onDeleted={() => setFilePath("")}
        />
      )}

      <Dialog
        open={keyPromptOpen}
        onOpenChange={(next) => !next && setKeyPromptOpen(false)}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Link a Jira issue</DialogTitle>
          </DialogHeader>
          <div className="grid gap-1.5 pt-1">
            <span className="text-[13px] font-medium text-muted-foreground">
              Issue key
            </span>
            <Input
              autoFocus
              value={keyInput}
              placeholder="e.g. ABC-123"
              onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && keyInput.trim())
                  void syncWithKey(keyInput.trim());
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setKeyPromptOpen(false)}
              disabled={syncJira.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => syncWithKey(keyInput.trim())}
              disabled={syncJira.isPending || !keyInput.trim()}
            >
              Link &amp; sync
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ThreadItem({
  icon,
  label,
  sub,
  active,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  sub?: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2.5 rounded px-2 py-1.5 text-left transition-colors duration-100",
        active ? "bg-primary/10" : "hover:bg-surface-3",
      )}
    >
      {icon}
      <span className="min-w-0 flex-1">
        <span
          className={cn(
            "block truncate text-[13px] font-medium",
            active ? "text-primary" : "text-foreground",
          )}
        >
          {label}
        </span>
        {sub && (
          <span className="block truncate text-[11px] text-muted-foreground">
            {sub}
          </span>
        )}
      </span>
    </button>
  );
}

function ViewToggle({
  active,
  onClick,
  icon,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  count?: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors duration-100",
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:bg-surface-1 hover:text-foreground",
      )}
    >
      {icon}
      {label}
      {count != null && count > 0 && (
        <span
          className={cn(
            "rounded-full px-1.5 text-[10px] tabular-nums",
            active ? "bg-primary/15" : "bg-surface-3 text-muted-foreground",
          )}
        >
          {count}
        </span>
      )}
    </button>
  );
}

// ── Right panel: task details (status / reporter / assignee) ──────────

function TaskDetailsPanel({
  task,
  canEdit,
  onClose,
}: {
  task: TaskDTO;
  canEdit: boolean;
  onClose: () => void;
}) {
  const board = useBoard(task.board_id);
  const members = useBoardMembers(task.board_id);
  const patch = usePatchTask(task.board_id);
  const columns = board.data?.columns ?? [];

  const memberOptions = useMemo(
    () =>
      (members.data ?? []).map((m) => ({
        id: m.user_id,
        name: m.display_name || m.email || m.user_id,
        email: m.email,
        avatar: m.avatar_url,
      })),
    [members.data],
  );
  const nameOf = (id?: string | null) => {
    if (!id) return "Unassigned";
    const m = (members.data ?? []).find((x) => x.user_id === id);
    return m?.display_name || m?.email || id;
  };
  const avatarOf = (id?: string | null) =>
    (members.data ?? []).find((x) => x.user_id === id)?.avatar_url ?? null;
  const current = columns.find((c) => c.key === task.status);

  // Jira issue-view "Details" panel: bold panel title, label column on the
  // left in small caps, values right.
  return (
    <div>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <UserRound className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-[13px] font-semibold text-foreground">
          Details
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Hide panel"
          className="ml-auto rounded p-1 text-muted-foreground transition-colors hover:bg-surface-1 hover:text-foreground"
        >
          <PanelRightClose className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="space-y-3.5 px-4 py-4">
        <DetailRow label="Status">
          {canEdit ? (
            <StatusSelect
              value={task.status}
              columns={columns}
              onChange={(key) =>
                patch.mutate({ taskId: task.id, body: { status: key } })
              }
            />
          ) : (
            <span
              className={cn(
                "inline-flex items-center rounded-sm px-2 py-1 text-[11.5px] font-bold uppercase tracking-[0.03em]",
                statusColor(task.status, current?.name).soft,
              )}
            >
              {current?.name ?? task.status}
            </span>
          )}
        </DetailRow>
        <DetailRow label="Reporter">
          <PersonInline
            name={nameOf(task.created_by)}
            avatar={avatarOf(task.created_by)}
          />
        </DetailRow>
        <DetailRow label="Assignee">
          {canEdit ? (
            <UserSelect
              className="w-full"
              options={memberOptions}
              value={task.assignee_id ?? null}
              onChange={(id) =>
                patch.mutate({ taskId: task.id, body: { assignee_id: id } })
              }
              placeholder="Unassigned"
              allowUnassigned
              loading={members.isLoading}
            />
          ) : (
            <PersonInline
              name={nameOf(task.assignee_id)}
              avatar={avatarOf(task.assignee_id)}
            />
          )}
        </DetailRow>
        <DetailRow label="Priority">
          {canEdit ? (
            <SelectMenu
              value={task.priority ?? ""}
              onChange={(v) =>
                patch.mutate({
                  taskId: task.id,
                  body: { priority: (v || null) as TaskDTO["priority"] },
                })
              }
              placeholder="None"
              options={[
                { value: "", label: "None", icon: <PriorityIcon priority={null} /> },
                ...PRIORITY_ORDER.map((p) => ({
                  value: p,
                  label: PRIORITY_META[p].label,
                  icon: <PriorityIcon priority={p} />,
                })),
              ]}
            />
          ) : (
            <span className="inline-flex items-center gap-1.5 pt-1.5 text-sm text-foreground">
              <PriorityIcon priority={task.priority} />
              {task.priority ? PRIORITY_META[task.priority].label : "None"}
            </span>
          )}
        </DetailRow>
      </div>
    </div>
  );
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-3">
      <span className="w-20 shrink-0 pt-1.5 text-[12px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
        {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function StatusSelect({
  value,
  columns,
  onChange,
  disabled,
}: {
  value: string;
  columns: BoardColumn[];
  onChange: (key: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const current = columns.find((c) => c.key === value);
  const col = statusColor(value, current?.name);
  return (
    <div className="relative">
      {/* Jira status lozenge: tinted, uppercase, square corners + chevron. */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-sm px-2 py-1 text-[11.5px] font-bold uppercase tracking-[0.03em] transition",
          col.soft,
          !disabled && "hover:brightness-95",
        )}
      >
        {current?.name ?? value}
        {!disabled && <ChevronDown className="h-3 w-3 opacity-70" />}
      </button>
      {open && (
        <>
          <button
            type="button"
            aria-hidden
            tabIndex={-1}
            className="fixed inset-0 z-10 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div className="absolute left-0 z-20 mt-1 max-h-64 w-52 overflow-auto rounded border border-border bg-popover p-1 shadow-overlay scrollbar-thin">
            {columns.map((c) => {
              const cc = statusColor(c.key, c.name);
              const active = c.key === value;
              return (
                <button
                  key={c.key}
                  type="button"
                  onClick={() => {
                    onChange(c.key);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded px-2 py-1.5 text-left text-[13px] transition-colors",
                    active
                      ? "bg-primary/10 font-medium text-primary"
                      : "text-foreground hover:bg-surface-1",
                  )}
                >
                  <span className={cn("h-2.5 w-2.5 shrink-0 rounded-sm", cc.dot)} />
                  <span className="truncate">{c.name}</span>
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function ViewModeButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors duration-100",
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:bg-surface-1 hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

// ── Overview: human notes / discussion ────────────────────────────────

function OverviewThread({
  task,
  canComment,
  onViewFile,
}: {
  task: TaskDTO;
  canComment: boolean;
  onViewFile: (path: string) => void;
}) {
  const taskId = task.id;
  const { client } = useApi();
  const me = useMe();
  const comments = useTaskComments(taskId);
  const activity = useTaskActivity(taskId);
  const board = useBoard(task.board_id);
  const members = useBoardMembers(task.board_id);
  const create = useCreateTaskComment(taskId);
  const del = useDeleteTaskComment(taskId);
  const patchTask = usePatchTask(task.board_id);
  const confirm = useConfirm();
  const [draft, setDraft] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [composerOpen, setComposerOpen] = useState(false);
  // Per-note agent visibility; resets to "visible" after posting/cancel.
  const [noteVisible, setNoteVisible] = useState(true);
  const [editingDesc, setEditingDesc] = useState(false);
  const [descDraft, setDescDraft] = useState("");
  const [viewMode, setViewMode] = useState<"comments" | "all">("comments");
  const fileRef = useRef<HTMLInputElement>(null);
  const att = usePendingAttachments<CommentAttachment>(
    (files) => client.uploadCommentAttachments(taskId, files),
    (dto) => client.deleteCommentAttachment(taskId, dto.path),
  );

  const nameOf = (id?: string | null) => {
    if (!id) return "Unassigned";
    const m = (members.data ?? []).find((x) => x.user_id === id);
    return m?.display_name || m?.email || id;
  };
  const statusOf = (key?: string | null) => {
    if (!key) return key ?? "";
    const col = (board.data?.columns ?? []).find((c) => c.key === key);
    return col?.name ?? key;
  };

  const timeline = useMemo(() => {
    const items: Array<
      | { kind: "comment"; at: string; comment: TaskCommentDTO }
      | { kind: "activity"; at: string; activity: TaskActivityDTO }
    > = [
      ...(comments.data ?? []).map((c) => ({
        kind: "comment" as const,
        at: c.created_at,
        comment: c,
      })),
      ...(activity.data ?? []).map((a) => ({
        kind: "activity" as const,
        at: a.created_at,
        activity: a,
      })),
    ];
    // Jira shows the newest entry first, right under the composer.
    items.sort((x, y) => y.at.localeCompare(x.at));
    return items;
  }, [comments.data, activity.data]);

  const visibleTimeline = useMemo(
    () =>
      viewMode === "all"
        ? timeline
        : timeline.filter((it) => it.kind === "comment"),
    [timeline, viewMode],
  );

  const description = task.description?.trim() ?? "";
  const loading = comments.isLoading || activity.isLoading;

  const meMember = (members.data ?? []).find(
    (m) => m.user_id === me.data?.user_id,
  );
  const myName =
    meMember?.display_name || meMember?.email || me.data?.email || "You";
  const myAvatar = meMember?.avatar_url ?? null;

  // Jira's "press M to comment" shortcut: opens + focuses the composer when
  // the user isn't already typing somewhere.
  useEffect(() => {
    if (!canComment) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "m" && e.key !== "M") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, [contenteditable='true']")) return;
      e.preventDefault();
      setComposerOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canComment]);

  const startEditDesc = () => {
    setDescDraft(description);
    setEditingDesc(true);
  };
  const saveDesc = () => {
    if (patchTask.isPending) return;
    const text = descDraft.trim();
    patchTask.mutate(
      { taskId, body: { description: text || null } },
      { onSuccess: () => setEditingDesc(false) },
    );
  };

  const submit = () => {
    const text = draft.trim();
    const attachments = att.serverItems();
    if ((!text && attachments.length === 0) || create.isPending || att.uploading)
      return;
    create.mutate(
      { body: text, attachments, visibleToAgents: noteVisible },
      {
        onSuccess: () => {
          setDraft("");
          att.clear();
          setNoteVisible(true);
          setComposerOpen(false);
        },
        // Surface failures — otherwise a rejected post looks like a dead button.
        onError: () => toast.error("Could not post the note. Please try again."),
      },
    );
  };

  const pickFiles = (files: FileList | null) => {
    if (files && files.length) void att.addFiles(Array.from(files));
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm({
      title: "Delete this note?",
      description:
        "The note is hidden from the discussion. This can't be undone from the UI.",
      confirmLabel: "Delete note",
      tone: "danger",
    });
    if (ok) del.mutate(id);
  };

  return (
    <>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <span className="text-[13px] font-semibold text-foreground">
          {viewMode === "all" ? "Activity" : "Discussion"}
        </span>
        <div className="ml-auto inline-flex gap-0.5">
          <ViewModeButton
            active={viewMode === "comments"}
            onClick={() => setViewMode("comments")}
            icon={<MessagesSquare className="h-3.5 w-3.5" />}
            label="Comments"
          />
          <ViewModeButton
            active={viewMode === "all"}
            onClick={() => setViewMode("all")}
            icon={<History className="h-3.5 w-3.5" />}
            label="All activity"
          />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
        <div className="mx-auto max-w-3xl px-4 py-4">
          {(description || canComment) && (
            <div className="mb-5">
              <div className="mb-1.5 flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
                <AlignLeft className="h-3.5 w-3.5 text-muted-foreground" /> Description
              </div>
              {editingDesc ? (
                <NoteEditor
                  value={descDraft}
                  onChange={setDescDraft}
                  onSubmit={saveDesc}
                  placeholder="Describe the task…"
                  autoFocus
                  footer={
                    <span className="ml-auto inline-flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditingDesc(false)}
                      >
                        Cancel
                      </Button>
                      <Button
                        size="sm"
                        onClick={saveDesc}
                        disabled={patchTask.isPending}
                      >
                        Save
                      </Button>
                    </span>
                  }
                />
              ) : description ? (
                // Jira-style: click the text to edit in place (editor role).
                <div
                  onClick={
                    canComment
                      ? (e) => {
                          // Let links inside the description stay clickable.
                          if ((e.target as HTMLElement).closest("a")) return;
                          startEditDesc();
                        }
                      : undefined
                  }
                  className={cn(
                    "-mx-1.5 rounded px-1.5 py-1",
                    canComment &&
                      "cursor-text transition-colors hover:bg-surface-1",
                  )}
                >
                  <div className="prose-chat prose-note max-w-none break-words">
                    <Markdown taskId={taskId}>{description}</Markdown>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={startEditDesc}
                  className="-mx-1.5 w-full rounded px-1.5 py-1 text-left text-[14px] text-muted-foreground/60 transition-colors hover:bg-surface-1"
                >
                  Add a description…
                </button>
              )}
            </div>
          )}

          {canComment ? (
            <div className="mb-6">
              <input
                ref={fileRef}
                type="file"
                multiple
                hidden
                onChange={(e) => {
                  pickFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              <div className="flex gap-3">
                <JiraAvatar
                  name={myName}
                  src={myAvatar}
                  size={32}
                  className="mt-0.5"
                />
                <div className="min-w-0 flex-1">
                  {composerOpen ? (
                    <div
                      onDragOver={(e) => {
                        e.preventDefault();
                        setDragOver(true);
                      }}
                      onDragLeave={() => setDragOver(false)}
                      onDrop={(e) => {
                        e.preventDefault();
                        setDragOver(false);
                        pickFiles(e.dataTransfer.files);
                      }}
                      className={cn(
                        "w-full rounded",
                        dragOver && "ring-2 ring-primary",
                      )}
                    >
                      <NoteEditor
                        value={draft}
                        onChange={setDraft}
                        onSubmit={submit}
                        onPasteFiles={(files) => void att.addFiles(files)}
                        placeholder="Add a note…"
                        autoFocus
                        attachments={
                          <AttachmentChips
                            items={att.items}
                            onRemove={att.remove}
                            className="px-3 pt-2"
                          />
                        }
                        footer={
                          <>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => fileRef.current?.click()}
                              title="Attach files"
                              className="shrink-0 text-muted-foreground hover:text-primary"
                            >
                              <Paperclip className="h-4 w-4" />
                            </Button>
                            <button
                              type="button"
                              onClick={() => setNoteVisible((v) => !v)}
                              title={
                                noteVisible
                                  ? "Agents can read this note — click to hide it from them"
                                  : "Hidden from agents — people only. Click to show it to agents"
                              }
                              className={cn(
                                "inline-flex shrink-0 items-center gap-1.5 rounded px-1.5 py-1 text-[12px] font-medium transition-colors",
                                noteVisible
                                  ? "text-muted-foreground hover:bg-surface-1 hover:text-foreground"
                                  : "bg-amber-50 text-amber-700 hover:bg-amber-100 dark:bg-amber-500/10 dark:text-amber-400 dark:hover:bg-amber-500/20",
                              )}
                            >
                              {noteVisible ? (
                                <Eye className="h-3.5 w-3.5" />
                              ) : (
                                <EyeOff className="h-3.5 w-3.5" />
                              )}
                              {noteVisible ? "Visible to agents" : "Hidden from agents"}
                            </button>
                            <span className="ml-auto inline-flex items-center gap-2">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => {
                                  setDraft("");
                                  att.discard();
                                  setNoteVisible(true);
                                  setComposerOpen(false);
                                }}
                              >
                                Cancel
                              </Button>
                              <Button
                                size="sm"
                                onClick={submit}
                                disabled={
                                  att.uploading ||
                                  (!draft.trim() && !att.hasReady) ||
                                  create.isPending
                                }
                              >
                                <Send className="h-3.5 w-3.5" /> Post
                              </Button>
                            </span>
                          </>
                        }
                      />
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setComposerOpen(true)}
                      className="h-10 w-full rounded border border-input bg-card px-3 text-left text-[14px] text-muted-foreground/60 transition-colors hover:border-border-strong"
                    >
                      Add a note…
                    </button>
                  )}
                  <p className="mt-1.5 text-[12px] text-muted-foreground">
                    <span className="font-semibold">Pro tip:</span> press{" "}
                    <kbd className="rounded border border-border bg-surface-1 px-1.5 py-0.5 font-sans text-[11px] font-medium text-foreground shadow-[0_1px_0_rgba(9,30,66,0.25)]">
                      M
                    </kbd>{" "}
                    to comment
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <p className="mb-6 text-[12px] text-muted-foreground">
              You have viewer access — posting notes requires editor role.
            </p>
          )}

          {loading ? (
            <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <Spinner className="h-3.5 w-3.5" /> loading…
            </div>
          ) : visibleTimeline.length === 0 ? (
            !description && (
              <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
                <span className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Sparkles className="h-6 w-6" />
                </span>
                <p className="text-sm font-medium text-foreground">
                  Task discussion & history
                </p>
                <p className="max-w-sm text-xs text-muted-foreground">
                  Notes are for people on the task. Switch to “All activity” to
                  see status/assignee changes. Agent chats live in their own
                  threads.
                </p>
              </div>
            )
          ) : (
            <ul className="flex flex-col gap-6">
              {visibleTimeline.map((it) =>
                it.kind === "comment" ? (
                  <CommentItem
                    key={`c:${it.comment.id}`}
                    taskId={taskId}
                    comment={it.comment}
                    canDelete={canComment}
                    canEditNote={
                      canComment && it.comment.author_id === me.data?.user_id
                    }
                    onDelete={() => void handleDelete(it.comment.id)}
                    onViewFile={onViewFile}
                  />
                ) : (
                  <ActivityItem
                    key={`a:${it.activity.id}`}
                    activity={it.activity}
                    nameOf={nameOf}
                    statusOf={statusOf}
                  />
                ),
              )}
            </ul>
          )}
        </div>
      </div>

    </>
  );
}

function CommentItem({
  taskId,
  comment,
  canDelete,
  canEditNote,
  onDelete,
  onViewFile,
}: {
  taskId: string;
  comment: TaskCommentDTO;
  canDelete: boolean;
  /** Author-only: backend rejects edits from anyone else. */
  canEditNote: boolean;
  onDelete: () => void;
  onViewFile: (path: string) => void;
}) {
  const name = comment.author_name || comment.author_id;
  const attachments = comment.attachments ?? [];
  const update = useUpdateTaskComment(taskId);
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState("");
  // Older notes predate the flag; treat a missing value as visible.
  const visibleToAgents = comment.visible_to_agents !== false;
  // created_at === updated_at on insert; >1s of drift means a real edit.
  const edited =
    new Date(comment.updated_at).getTime() -
      new Date(comment.created_at).getTime() >
    1000;

  const startEdit = () => {
    setEditDraft(comment.body ?? "");
    setEditing(true);
  };
  const saveEdit = () => {
    const text = editDraft.trim();
    if ((!text && attachments.length === 0) || update.isPending) return;
    update.mutate(
      { commentId: comment.id, body: text },
      { onSuccess: () => setEditing(false) },
    );
  };

  // Jira-style plain comment: avatar + bold name, full timestamp on its own
  // line, body, then a text action row underneath (Copy · Edit · Delete).
  return (
    <li className="flex gap-3">
      <JiraAvatar
        name={name}
        src={comment.author_avatar}
        size={32}
        className="mt-0.5"
      />
      <div className="min-w-0 flex-1">
        <div className="min-w-0">
          <p className="truncate text-[14px] font-semibold leading-[18px] text-foreground">
            {name}
          </p>
          <p className="mt-px flex items-center gap-1.5 text-[12px] leading-4 text-muted-foreground">
            {fmtTimeFull(comment.created_at)}
            {edited && " (edited)"}
            {!visibleToAgents && (
              <span
                title="People-only note — agents never see it in their context"
                className="inline-flex items-center gap-1 rounded bg-amber-50 px-1.5 py-px font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-400"
              >
                <EyeOff className="h-3 w-3" /> Hidden from agents
              </span>
            )}
          </p>
        </div>
        {editing ? (
          <div className="mt-2">
            <NoteEditor
              value={editDraft}
              onChange={setEditDraft}
              onSubmit={saveEdit}
              autoFocus
              footer={
                <span className="ml-auto inline-flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setEditing(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={saveEdit}
                    disabled={
                      update.isPending ||
                      (!editDraft.trim() && attachments.length === 0)
                    }
                  >
                    Save
                  </Button>
                </span>
              }
            />
          </div>
        ) : (
          comment.body && (
            <div className="prose-chat prose-note mt-1.5 max-w-none break-words">
              <Markdown taskId={taskId}>{comment.body}</Markdown>
            </div>
          )
        )}
        {attachments.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {attachments.map((a) => (
              <NoteAttachment
                key={a.id}
                taskId={taskId}
                att={a}
                onView={() => onViewFile(a.path)}
              />
            ))}
          </div>
        )}
        {!editing && (
          <div className="mt-1.5 flex items-center gap-1 text-[12px] font-medium text-muted-foreground">
            {comment.body && (
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard.writeText(comment.body ?? "");
                  toast.success("Note copied");
                }}
                className="rounded px-1 py-0.5 transition-colors hover:bg-surface-1 hover:text-foreground"
              >
                Copy text
              </button>
            )}
            {comment.body && canEditNote && (
              <span className="text-border-strong">·</span>
            )}
            {canEditNote && (
              <button
                type="button"
                onClick={startEdit}
                className="rounded px-1 py-0.5 transition-colors hover:bg-surface-1 hover:text-foreground"
              >
                Edit
              </button>
            )}
            {canEditNote && <span className="text-border-strong">·</span>}
            {canEditNote && (
              <button
                type="button"
                disabled={update.isPending}
                title={
                  visibleToAgents
                    ? "Stop including this note in agent context"
                    : "Let agents read this note again"
                }
                onClick={() =>
                  update.mutate(
                    { commentId: comment.id, visibleToAgents: !visibleToAgents },
                    {
                      onError: () =>
                        toast.error("Could not change the note's visibility."),
                    },
                  )
                }
                className="rounded px-1 py-0.5 transition-colors hover:bg-surface-1 hover:text-foreground disabled:opacity-50"
              >
                {visibleToAgents ? "Hide from agents" : "Show to agents"}
              </button>
            )}
            {(comment.body || canEditNote) && canDelete && (
              <span className="text-border-strong">·</span>
            )}
            {canDelete && (
              <button
                type="button"
                onClick={onDelete}
                className="rounded px-1 py-0.5 transition-colors hover:bg-surface-1 hover:text-destructive"
              >
                Delete
              </button>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

/** One note attachment: thumbnail/chip that opens the shared file viewer. */
function NoteAttachment({
  taskId,
  att,
  onView,
}: {
  taskId: string;
  att: CommentAttachment;
  onView: () => void;
}) {
  const isImage = att.media_type.startsWith("image/");
  const blob = useTaskFileBlobUrl(taskId, att.path);

  if (isImage) {
    if (blob.data) {
      return (
        <button
          type="button"
          onClick={onView}
          title={att.filename}
          className="block overflow-hidden rounded border border-border transition hover:border-primary"
        >
          <img
            src={blob.data}
            alt={att.filename}
            className="max-h-44 max-w-[14rem] object-cover"
          />
        </button>
      );
    }
    return (
      <div className="flex h-24 w-32 items-center justify-center rounded border border-border bg-surface-1">
        {blob.isError ? (
          <span className="px-2 text-center text-[11px] text-rose-500">
            failed to load
          </span>
        ) : (
          <Spinner className="h-4 w-4 text-muted-foreground" />
        )}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={onView}
      className="inline-flex max-w-[14rem] items-center gap-2 rounded border border-border bg-surface-1 px-2.5 py-1.5 text-xs font-medium text-foreground transition hover:border-primary hover:text-primary"
    >
      <FileText className="h-4 w-4 shrink-0 text-primary" />
      <span className="truncate">{att.filename}</span>
    </button>
  );
}

function PersonInline({
  name,
  avatar,
}: {
  name: string;
  avatar?: string | null;
}) {
  return (
    <span className="inline-flex items-center gap-2 font-ui text-[14px] leading-5 text-foreground">
      <JiraAvatar name={name} src={avatar} size={24} />
      <span className="truncate">{name}</span>
    </span>
  );
}

const ACTIVITY_STYLE: Record<
  string,
  { icon: typeof CircleDot; tone: string }
> = {
  created: { icon: Plus, tone: "bg-sky-100 text-sky-600 dark:bg-sky-500/15" },
  status_changed: {
    icon: ArrowRight,
    tone: "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15",
  },
  assignee_changed: {
    icon: UserRound,
    tone: "bg-violet-100 text-violet-600 dark:bg-violet-500/15",
  },
  title_changed: {
    icon: Pencil,
    tone: "bg-amber-100 text-amber-600 dark:bg-amber-500/15",
  },
  archived: {
    icon: Archive,
    tone: "bg-rose-100 text-rose-600 dark:bg-rose-500/15",
  },
};

function ActivityItem({
  activity,
  nameOf,
  statusOf,
}: {
  activity: TaskActivityDTO;
  nameOf: (id?: string | null) => string;
  statusOf: (key?: string | null) => string;
}) {
  const actor = activity.actor_name || activity.actor_id || "Someone";
  const style = ACTIVITY_STYLE[activity.kind] ?? {
    icon: CircleDot,
    tone: "bg-surface-3 text-muted-foreground",
  };
  const Icon = style.icon;
  const d = activity.data;

  let detail: React.ReactNode;
  switch (activity.kind) {
    case "created":
      detail = "created this task";
      break;
    case "status_changed":
      detail = (
        <>
          changed status{" "}
          <b className="font-medium text-foreground">{statusOf(d.from)}</b>{" "}
          <ArrowRight className="inline h-3 w-3 -translate-y-px text-muted-foreground" />{" "}
          <b className="font-medium text-foreground">{statusOf(d.to)}</b>
        </>
      );
      break;
    case "assignee_changed":
      detail = d.to ? (
        <>
          assigned to{" "}
          <b className="font-medium text-foreground">{nameOf(d.to)}</b>
        </>
      ) : (
        "removed the assignee"
      );
      break;
    case "title_changed":
      detail = "edited the title";
      break;
    case "archived":
      detail = "archived this task";
      break;
    default:
      detail = activity.kind.replace(/_/g, " ");
  }

  return (
    <li className="flex items-center gap-2.5 px-1 text-[12.5px] text-muted-foreground">
      <span
        className={cn(
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
          style.tone,
        )}
      >
        <Icon className="h-3 w-3" />
      </span>
      <span className="font-medium text-foreground">{actor}</span>
      <span className="min-w-0 truncate">{detail}</span>
      <span className="ml-auto shrink-0 text-[11px] text-muted-foreground/80">
        {fmtTime(activity.created_at)}
      </span>
    </li>
  );
}

// ── Agent conversation ────────────────────────────────────────────────

/** "An is typing…" / "An and Bình are typing…" / "An and 2 others are typing…" */
function formatTypingNames(names: string[]): string {
  if (names.length === 1) return `${names[0]} is typing…`;
  if (names.length === 2) return `${names[0]} and ${names[1]} are typing…`;
  return `${names[0]} and ${names.length - 1} others are typing…`;
}

function Conversation({
  taskId,
  agentId,
  agentName,
  canEdit,
  workspaceRoot,
  onOpenFile,
}: {
  taskId: string;
  agentId: string;
  agentName: string;
  canEdit: boolean;
  workspaceRoot: string;
  onOpenFile: (path: string) => void;
}) {
  const { client } = useApi();
  const { blocks, running, loadingHistory, fatalError, send, cancel } =
    useTaskAgentRun(taskId, agentId);
  const { typingNames, notifyTyping, stopTyping } = useTypingIndicator(
    taskId,
    agentId,
  );
  const [draft, setDraft] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [view, setView] = useState<"thread" | "changes">("thread");
  const changedCount = useMemo(() => changedFileCount(blocks), [blocks]);
  // Workspace files dropped from the Artifacts tree. Shown to the user as chips,
  // serialized as `[file:<path>]` tokens in the prompt so the agent can read them.
  const [linkedFiles, setLinkedFiles] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const att = usePendingAttachments(
    (files) => client.uploadTaskAttachments(taskId, agentId, files),
    (dto) => client.deleteTaskAttachment(taskId, agentId, dto.id),
  );

  // Artifacts paths are workspace-relative; the agent's read tool resolves
  // relative paths against the sandbox base (not the task subdir), so we hand
  // it the absolute path under the task workspace to avoid a `find` fallback.
  const toAbsolute = (rel: string) => {
    if (rel.startsWith("/")) return rel;
    const root = (workspaceRoot || "").replace(/\/+$/, "");
    return root ? `${root}/${rel}` : rel;
  };
  const addLinkedFile = (rel: string) => {
    const abs = toAbsolute(rel);
    setLinkedFiles((prev) => (prev.includes(abs) ? prev : [...prev, abs]));
  };
  const removeLinkedFile = (path: string) =>
    setLinkedFiles((prev) => prev.filter((p) => p !== path));

  const submit = () => {
    const body = draft.trim();
    if (running || att.uploading) return;
    const attachments = att.toUserAttachments();
    const links = linkedFiles.map((p) => `[file:${p}]`).join(" ");
    const text = [links, body].filter(Boolean).join("\n");
    if (!text && attachments.length === 0) return;
    void send(text, attachments);
    setDraft("");
    setLinkedFiles([]);
    att.clear();
    stopTyping();
  };

  return (
    <>
      {changedCount > 0 && (
        <div className="flex items-center gap-1 border-b border-border px-3 py-1.5">
          <ViewToggle
            active={view === "thread"}
            onClick={() => setView("thread")}
            icon={<MessagesSquare className="h-3.5 w-3.5" />}
            label="Thread"
          />
          <ViewToggle
            active={view === "changes"}
            onClick={() => setView("changes")}
            icon={<FileDiff className="h-3.5 w-3.5" />}
            label="Changes"
            count={changedCount}
          />
        </div>
      )}
      {view === "changes" ? (
        <RunChanges blocks={blocks} onOpenFile={onOpenFile} />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
          {loadingHistory && blocks.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
              <Spinner className="h-6 w-6 text-brand-400" />
              Loading conversation…
            </div>
          ) : blocks.length === 0 && !running ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
              <span className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Bot className="h-6 w-6" />
              </span>
              Message {`@${agentName}`} to get started.
            </div>
          ) : (
            <Timeline blocks={blocks} running={running} onOpenFile={onOpenFile} />
          )}
          {fatalError && (
            <div className="mx-auto max-w-3xl px-4 pb-3 text-xs text-destructive">
              {fatalError}
            </div>
          )}
        </div>
      )}

      {canEdit ? (
        <div className="border-t border-border p-3">
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files?.length)
                void att.addFiles(Array.from(e.target.files));
              e.target.value = "";
            }}
          />
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const artifactPath = e.dataTransfer.getData(ARTIFACT_DND_TYPE);
              if (artifactPath) {
                addLinkedFile(artifactPath);
                return;
              }
              if (e.dataTransfer.files?.length)
                void att.addFiles(Array.from(e.dataTransfer.files));
            }}
            className={cn(
              "mx-auto w-full max-w-3xl rounded",
              dragOver && "ring-2 ring-primary",
            )}
          >
            {typingNames.length > 0 && (
              <div className="mb-1 flex items-center gap-1.5 px-1 text-xs italic text-muted-foreground">
                <span className="flex gap-0.5">
                  <span className="h-1 w-1 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.3s]" />
                  <span className="h-1 w-1 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.15s]" />
                  <span className="h-1 w-1 animate-bounce rounded-full bg-muted-foreground/60" />
                </span>
                {formatTypingNames(typingNames)}
              </div>
            )}
            {/* Single framed composer (same pattern as the notes editor):
                chips + textarea + action bar live inside one bordered box. */}
            <div className="overflow-hidden rounded border border-input bg-card transition-colors duration-100 hover:border-border-strong focus-within:border-[#4C9AFF] focus-within:hover:border-[#4C9AFF]">
              <LinkedFileChips
                paths={linkedFiles}
                onRemove={removeLinkedFile}
                className="px-3 pt-2"
              />
              <AttachmentChips
                items={att.items}
                onRemove={att.remove}
                className="px-3 pt-2"
              />
              <textarea
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  if (e.target.value.trim()) notifyTyping();
                }}
                onPaste={(e) => {
                  const files = Array.from(e.clipboardData.files);
                  if (files.length) {
                    e.preventDefault();
                    void att.addFiles(files);
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    submit();
                  }
                }}
                rows={2}
                placeholder={`Message @${agentName}…`}
                className="block min-h-[2.5rem] w-full resize-none border-0 bg-transparent px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/50 focus:outline-none"
              />
              <div className="flex items-center justify-between gap-2 border-t border-border px-2 py-1.5">
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => fileRef.current?.click()}
                  title="Attach files or images"
                  disabled={running}
                  className="shrink-0 text-muted-foreground hover:text-primary"
                >
                  <Paperclip className="h-4 w-4" />
                </Button>
                {running ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void cancel()}
                  >
                    <CircleSlash className="h-4 w-4" /> Stop
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    onClick={submit}
                    disabled={
                      att.uploading ||
                      (!draft.trim() &&
                        !att.hasReady &&
                        linkedFiles.length === 0)
                    }
                  >
                    <Send className="h-3.5 w-3.5" /> Send
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="border-t border-border px-4 py-3 text-center text-xs text-muted-foreground">
          You have viewer access — mentioning requires editor role.
        </div>
      )}
    </>
  );
}

/** Dropdown listing past conversation attempts for the (task, agent) pair. */
function AttemptHistoryMenu({
  attempts,
  activeConvId,
  viewConvId,
  onSelect,
}: {
  attempts: AttemptDTO[];
  activeConvId: string | undefined;
  viewConvId: string | null;
  onSelect: (a: AttemptDTO) => void;
}) {
  const currentConvId = viewConvId ?? activeConvId;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-surface-1 hover:text-foreground"
          title="Conversation history"
        >
          <History className="h-3.5 w-3.5" /> History
          <ChevronDown className="h-3 w-3" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-80 w-72 overflow-auto">
        {attempts.map((a) => {
          const selected = a.conv_id === currentConvId;
          return (
            <DropdownMenuItem
              key={a.conv_id}
              onSelect={() => onSelect(a)}
              className="flex-col items-start gap-0.5"
            >
              <div className="flex w-full items-center gap-2">
                <span className="truncate text-[13px] font-medium">
                  {a.title?.trim() || `Attempt #${a.attempt}`}
                </span>
                {a.is_active && (
                  <span className="ml-auto shrink-0 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">
                    Active
                  </span>
                )}
                {selected && !a.is_active && (
                  <Check className="ml-auto h-3.5 w-3.5 shrink-0 text-brand-500" />
                )}
              </div>
              <span className="text-[11px] text-muted-foreground">
                #{a.attempt} · {fmtTime(a.created_at)}
              </span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Read-only transcript of a past attempt (selected from the history menu). */
function AttemptHistoryView({
  taskId,
  agentId,
  convId,
  attempt,
  onOpenFile,
  onReturnToLive,
}: {
  taskId: string;
  agentId: string;
  convId: string;
  attempt: number | undefined;
  onOpenFile: (path: string) => void;
  onReturnToLive: () => void;
}) {
  const messages = useTaskAttemptMessages(taskId, agentId, convId);
  const blocks = useMemo(
    () => blocksFromHistory(messages.data ?? [], null),
    [messages.data],
  );
  return (
    <>
      <div className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-xs text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
        <History className="h-3.5 w-3.5 shrink-0" />
        <span>
          Viewing archived conversation
          {attempt != null ? ` (attempt #${attempt})` : ""} — read-only
        </span>
        <button
          type="button"
          onClick={onReturnToLive}
          className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-medium transition-colors hover:bg-amber-100 dark:hover:bg-amber-500/20"
        >
          <ArrowRight className="h-3.5 w-3.5" /> Back to live
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
        {messages.isLoading ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
            <Spinner className="h-6 w-6 text-brand-400" />
            Loading conversation…
          </div>
        ) : blocks.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-sm text-muted-foreground">
            This conversation has no messages.
          </div>
        ) : (
          <Timeline blocks={blocks} running={false} onOpenFile={onOpenFile} />
        )}
      </div>
    </>
  );
}

/** Chips for workspace files linked into an agent message (drag from Artifacts). */
function LinkedFileChips({
  paths,
  onRemove,
  className,
}: {
  paths: string[];
  onRemove: (path: string) => void;
  className?: string;
}) {
  if (paths.length === 0) return null;
  return (
    <div className={cn("flex flex-wrap gap-1.5", className)}>
      {paths.map((p) => {
        const name = p.split("/").pop() || p;
        return (
          <span
            key={p}
            title={p}
            className="inline-flex max-w-[16rem] items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 py-1 pl-2 pr-1 text-xs font-medium text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300"
          >
            <FileText className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{name}</span>
            <button
              type="button"
              onClick={() => onRemove(p)}
              aria-label={`Remove ${name}`}
              className="rounded p-0.5 text-amber-500 transition-colors hover:bg-amber-100 hover:text-amber-700 dark:hover:bg-amber-500/20"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        );
      })}
    </div>
  );
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/** Jira-style full timestamp under a comment author: "April 27, 2026 at 11:48 PM". */
function fmtTimeFull(iso: string): string {
  try {
    const d = new Date(iso);
    const date = d.toLocaleDateString(undefined, {
      month: "long",
      day: "numeric",
      year: "numeric",
    });
    const time = d.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    });
    return `${date} at ${time}`;
  } catch {
    return iso;
  }
}
