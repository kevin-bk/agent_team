import {
  ArrowLeft,
  Bot,
  Calendar,
  Check,
  ChevronDown,
  Columns3,
  Download,
  FileText,
  Gauge,
  GitBranch,
  List,
  Plus,
  Search,
  Settings,
  Tag,
  Users,
  X,
} from "@/components/icons";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";
import { useApi } from "@/api/ApiProvider";
import {
  useAgents,
  useAutopilot,
  useBoard,
  useBoardMembers,
  useBoardTasks,
  useCliTargets,
  useMoveTask,
} from "@/api/hooks";
import type { BoardMemberDTO, TaskDTO } from "@/api/types";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { BoardImportDialog } from "./BoardImportDialog";
import { AvatarGroup, Breadcrumbs, JiraAvatar, JiraIcon } from "@/components/jira";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ConfirmDialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { BoardEventsProvider } from "./BoardEventsContext";
import { BoardAgentsDialog } from "./BoardAgentsDialog";
import { BoardAutopilotDialog } from "./BoardAutopilotDialog";
import { BoardReposDialog } from "./BoardReposDialog";
import { BoardJiraDialog } from "./BoardJiraDialog";
import { BoardJiraSyncDialog } from "./BoardJiraSyncDialog";
import { BoardSettingsDialog } from "./BoardSettingsDialog";
import { Column } from "./Column";
import { MembersDialog } from "./MembersDialog";
import { TaskCockpit } from "./TaskCockpit";
import { TaskDialog } from "./TaskDialog";
import { tasksInColumn } from "./reorder";
import { type MoveArgs, useBoardDnd } from "./useBoardDnd";

function matchesQuery(task: TaskDTO, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  return (
    task.title.toLowerCase().includes(needle) ||
    task.human_key.toLowerCase().includes(needle) ||
    (task.jira_key?.toLowerCase().includes(needle) ?? false) ||
    task.labels.some((l) => l.toLowerCase().includes(needle))
  );
}

/** Sentinel filter value for "no assignee / no agent". */
const UNASSIGNED = "__unassigned__";

/** Multi-select OR-match against a task field (empty selection = match all). */
function matchesSelection(
  selected: string[],
  value: string | null | undefined,
): boolean {
  if (selected.length === 0) return true;
  if (!value) return selected.includes(UNASSIGNED);
  return selected.includes(value);
}

interface BoardFilters {
  assignee: string[];
  agent: string[];
  label: string[];
}

const EMPTY_FILTERS: BoardFilters = { assignee: [], agent: [], label: [] };

/** Per-board filter selections persist in localStorage so F5 keeps them. */
function filtersKey(boardId: string): string {
  return `agent_team:board:${boardId}:filters`;
}

function loadFilters(boardId: string): BoardFilters {
  try {
    const raw = localStorage.getItem(filtersKey(boardId));
    if (!raw) return EMPTY_FILTERS;
    const parsed = JSON.parse(raw) as Partial<BoardFilters>;
    const arr = (v: unknown) =>
      Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
    return {
      assignee: arr(parsed.assignee),
      agent: arr(parsed.agent),
      label: arr(parsed.label),
    };
  } catch {
    return EMPTY_FILTERS;
  }
}

function saveFilters(boardId: string, filters: BoardFilters): void {
  try {
    const empty =
      filters.assignee.length === 0 &&
      filters.agent.length === 0 &&
      filters.label.length === 0;
    if (empty) localStorage.removeItem(filtersKey(boardId));
    else localStorage.setItem(filtersKey(boardId), JSON.stringify(filters));
  } catch {
    // Ignore storage failures (private mode / quota) — filters still work in-session.
  }
}

interface BoardViewProps {
  boardId: string;
  cockpitTaskKey: string | null;
  onBack: () => void;
  onOpenTask: (taskKey: string) => void;
  onCloseTask: () => void;
}

export function BoardView(props: BoardViewProps) {
  // One realtime connection for the whole board, kept mounted across the
  // list ⇄ cockpit switch so multi-user changes stream in without an F5.
  return (
    <BoardEventsProvider boardId={props.boardId}>
      <BoardViewInner key={props.boardId} {...props} />
    </BoardEventsProvider>
  );
}

function BoardViewInner({
  boardId,
  cockpitTaskKey,
  onBack,
  onOpenTask,
  onCloseTask,
}: BoardViewProps) {
  const board = useBoard(boardId);
  const autopilot = useAutopilot(boardId);
  const tasksQuery = useBoardTasks(boardId);
  const move = useMoveTask(boardId);

  const members = useBoardMembers(boardId);
  const agents = useAgents();
  const cliTargets = useCliTargets();
  const [dialog, setDialog] = useState<
    { mode: "create"; status: string } | { mode: "edit"; task: TaskDTO } | null
  >(null);
  const [membersOpen, setMembersOpen] = useState(false);
  const initialFilters = useMemo(() => loadFilters(boardId), [boardId]);
  const [assigneeFilter, setAssigneeFilter] = useState<string[]>(
    initialFilters.assignee,
  );
  const [agentFilter, setAgentFilter] = useState<string[]>(initialFilters.agent);
  const [labelFilter, setLabelFilter] = useState<string[]>(initialFilters.label);
  // Persist selections per board so a reload (F5) keeps the same filters.
  useEffect(() => {
    saveFilters(boardId, {
      assignee: assigneeFilter,
      agent: agentFilter,
      label: labelFilter,
    });
  }, [boardId, assigneeFilter, agentFilter, labelFilter]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [autopilotOpen, setAutopilotOpen] = useState(false);
  const [reposOpen, setReposOpen] = useState(false);
  const [jiraOpen, setJiraOpen] = useState(false);
  const [jiraSyncOpen, setJiraSyncOpen] = useState(false);
  // When set, the import preview is scoped to these specific keys.
  const [jiraSyncKeys, setJiraSyncKeys] = useState<string[] | undefined>(
    undefined,
  );
  const [importOpen, setImportOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [query, setQuery] = useState("");
  const { client } = useApi();
  const confirm = useConfirm();

  const onExport = useCallback(async () => {
    const ok = await confirm({
      title: "Export tasks to CSV?",
      description:
        "This downloads every task on this board (title, status, priority, labels, assignee and more) as a CSV file.",
      confirmLabel: "Export",
    });
    if (!ok) return;
    setExporting(true);
    try {
      const csv = await client.exportBoardTasksCsv(boardId);
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${board.data?.slug ?? "board"}-tasks.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }, [client, boardId, board.data?.slug, confirm]);

  const tasks = useMemo(() => tasksQuery.data ?? [], [tasksQuery.data]);
  const visibleTasks = useMemo(
    () =>
      tasks.filter(
        (t) =>
          matchesQuery(t, query) &&
          matchesSelection(assigneeFilter, t.assignee_id) &&
          matchesSelection(agentFilter, t.agent_assignee) &&
          (labelFilter.length === 0 ||
            t.labels.some((l) => labelFilter.includes(l))),
      ),
    [tasks, query, assigneeFilter, agentFilter, labelFilter],
  );
  const membersById = useMemo(() => {
    const map = new Map<string, BoardMemberDTO>();
    for (const m of members.data ?? []) map.set(m.user_id, m);
    return map;
  }, [members.data]);

  // Filter option lists ────────────────────────────────────────────────
  const memberOptions = useMemo<FilterOption[]>(
    () =>
      (members.data ?? []).map((m) => ({
        id: m.user_id,
        label: m.display_name || m.email || m.user_id,
        avatar: m.avatar_url,
        sub: m.email,
      })),
    [members.data],
  );
  const agentOptions = useMemo<FilterOption[]>(() => {
    const staffedAgents = new Set(board.data?.agent_ids ?? []);
    const staffedClis = new Set(board.data?.cli_target_ids ?? []);
    const out: FilterOption[] = [];
    for (const a of agents.data ?? [])
      if (staffedAgents.has(a.id)) out.push({ id: a.id, label: a.display_name });
    for (const t of cliTargets.data ?? [])
      if (staffedClis.has(t.id))
        out.push({ id: t.id, label: `${t.label} (direct)` });
    return out;
  }, [board.data?.agent_ids, board.data?.cli_target_ids, agents.data, cliTargets.data]);
  const labelOptions = useMemo<FilterOption[]>(() => {
    const seen = new Set<string>();
    for (const t of tasks) for (const l of t.labels) seen.add(l);
    return [...seen].sort().map((l) => ({ id: l, label: l }));
  }, [tasks]);

  const toggleIn = (setter: typeof setAssigneeFilter) => (id: string) =>
    setter((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  const clearAllFilters = () => {
    setAssigneeFilter([]);
    setAgentFilter([]);
    setLabelFilter([]);
  };
  const activeFilterCount =
    assigneeFilter.length + agentFilter.length + labelFilter.length;
  const taskCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const t of tasks) counts[t.status] = (counts[t.status] ?? 0) + 1;
    return counts;
  }, [tasks]);
  const canEdit =
    board.data?.my_role === "owner" || board.data?.my_role === "editor";

  // Keep the latest tasks + move callback in refs so the DnD monitor (which
  // subscribes once) always sees current data without re-subscribing.
  const tasksRef = useRef<TaskDTO[]>([]);
  tasksRef.current = tasks;
  const onMoveRef = useRef<(args: MoveArgs) => void>(() => {});
  onMoveRef.current = (args) =>
    move.mutate({
      taskId: args.taskId,
      body: { status: args.status, position: args.position },
    });
  useBoardDnd(tasksRef, onMoveRef);

  const closeDialog = useCallback(() => setDialog(null), []);

  if (board.isLoading || tasksQuery.isLoading) {
    return (
      <div className="flex h-full gap-3 overflow-hidden px-8 py-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex w-72 shrink-0 flex-col gap-2">
            <Skeleton className="h-7 w-32" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ))}
      </div>
    );
  }
  if (board.isError || !board.data) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
        <p>Couldn't load this board (you may not have access).</p>
        <Button variant="outline" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" /> Back to boards
        </Button>
      </div>
    );
  }

  const columns = board.data.columns;
  const cockpitTask =
    tasks.find((t) => t.human_key === cockpitTaskKey) ?? null;

  // Full-screen task cockpit (Activity | Conversation | Artifacts).
  if (cockpitTask) {
    return (
      <>
        <TaskCockpit
          task={cockpitTask}
          canEdit={canEdit}
          onBack={onCloseTask}
          onEdit={() => setDialog({ mode: "edit", task: cockpitTask })}
        />
        <TaskDialog
          boardId={boardId}
          columns={columns}
          open={dialog?.mode === "edit"}
          task={dialog?.mode === "edit" ? dialog.task : null}
          defaultStatus={columns[0]?.key ?? "todo"}
          onClose={closeDialog}
        />
      </>
    );
  }

  // Classic-Jira board page: breadcrumbs + a big 24px title sitting directly
  // on the white page (no boxed header strips), then a filter row with the
  // search field, the lifting avatar stack and borderless filter buttons.
  return (
    <div className="font-ui flex h-full flex-col bg-background">
      <div className="flex flex-col px-8 pt-6">
        <Breadcrumbs
          items={[
            { label: "Projects" },
            { label: "Boards", onClick: onBack },
            { label: board.data.name },
          ]}
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <h1 className="truncate text-[20px] font-semibold text-foreground">
              {board.data.name}
            </h1>
            <RoleBadge role={board.data.my_role} />
          </div>

          <div className="ml-auto flex items-center gap-2">
            {canEdit && (
              <Button
                variant="ghost"
                aria-label="Jira sync"
                onClick={() => setJiraOpen(true)}
              >
                <JiraIcon className="h-4 w-4" /> Jira
              </Button>
            )}
            {canEdit && (
              <Button
                variant="ghost"
                aria-label="Board agents"
                onClick={() => setAgentsOpen(true)}
              >
                <Bot className="h-4 w-4" /> Agents
              </Button>
            )}
            {canEdit && (
              <Button
                variant="ghost"
                aria-label="Board autopilot"
                onClick={() => setAutopilotOpen(true)}
              >
                <Gauge className="h-4 w-4" /> Autopilot
                {autopilot.data?.enabled && (
                  <span
                    className="ml-0.5 h-2 w-2 rounded-full bg-emerald-500"
                    title="Autopilot is on"
                  />
                )}
              </Button>
            )}
            {canEdit && (
              <Button
                variant="ghost"
                aria-label="Board repositories"
                onClick={() => setReposOpen(true)}
              >
                <GitBranch className="h-4 w-4" /> Code
              </Button>
            )}
            {canEdit && (
              <Button
                variant="ghost"
                aria-label="Import tasks from CSV"
                onClick={() => setImportOpen(true)}
              >
                <FileText className="h-4 w-4" /> Import
              </Button>
            )}
            <Button
              variant="ghost"
              aria-label="Export tasks to CSV"
              onClick={onExport}
              disabled={exporting}
            >
              {exporting ? (
                <Spinner className="h-4 w-4" />
              ) : (
                <Download className="h-4 w-4" />
              )}
              {exporting ? "Exporting…" : "Export"}
            </Button>
            {canEdit && (
              <Button
                variant="ghost"
                aria-label="Board settings"
                onClick={() => setSettingsOpen(true)}
              >
                <Settings className="h-4 w-4" /> Settings
              </Button>
            )}
            {canEdit && (
              <Button
                onClick={() =>
                  setDialog({ mode: "create", status: columns[0]?.key ?? "todo" })
                }
              >
                <Plus className="h-4 w-4" /> Create
              </Button>
            )}
          </div>
        </div>

        {/* View tab strip (current Jira: blue underline on the active tab). */}
        <div className="mt-2 flex items-center border-b border-border">
          <PageTab icon={<Columns3 className="h-4 w-4" />} label="Board" active />
          <PageTab icon={<List className="h-4 w-4" />} label="List" />
          <PageTab icon={<Calendar className="h-4 w-4" />} label="Timeline" />
        </div>

        <FilterBar
          query={query}
          onQuery={setQuery}
          memberOptions={memberOptions}
          agentOptions={agentOptions}
          labelOptions={labelOptions}
          assigneeFilter={assigneeFilter}
          agentFilter={agentFilter}
          labelFilter={labelFilter}
          onToggleAssignee={toggleIn(setAssigneeFilter)}
          onToggleAgent={toggleIn(setAgentFilter)}
          onToggleLabel={toggleIn(setLabelFilter)}
          activeFilterCount={activeFilterCount}
          onClearFilters={clearAllFilters}
          onManageMembers={() => setMembersOpen(true)}
        />
      </div>

      <div className="group/board flex flex-1 gap-2 overflow-x-auto bg-background px-8 pb-6 pt-4">
        {columns.map((column) => (
          <Column
            key={column.key}
            column={column}
            tasks={tasksInColumn(visibleTasks, column.key)}
            canEdit={canEdit}
            membersById={membersById}
            compact={columns.length <= 8}
            onTaskClick={(task) => onOpenTask(task.human_key)}
            onEditTask={(task) => setDialog({ mode: "edit", task })}
            onAddTask={(status) => setDialog({ mode: "create", status })}
          />
        ))}
      </div>

      <TaskDialog
        boardId={boardId}
        columns={columns}
        open={dialog !== null}
        task={dialog?.mode === "edit" ? dialog.task : null}
        defaultStatus={dialog?.mode === "create" ? dialog.status : columns[0]?.key ?? "todo"}
        onClose={closeDialog}
      />

      <MembersDialog
        boardId={boardId}
        canManage={board.data.my_role === "owner"}
        open={membersOpen}
        onClose={() => setMembersOpen(false)}
      />

      <BoardAgentsDialog
        board={board.data}
        open={agentsOpen}
        onClose={() => setAgentsOpen(false)}
      />

      <BoardAutopilotDialog
        board={board.data}
        open={autopilotOpen}
        onClose={() => setAutopilotOpen(false)}
      />

      <BoardReposDialog
        boardId={boardId}
        open={reposOpen}
        onClose={() => setReposOpen(false)}
      />

      <BoardImportDialog
        boardId={boardId}
        open={importOpen}
        onClose={() => setImportOpen(false)}
      />

      <BoardJiraDialog
        board={board.data}
        open={jiraOpen}
        onClose={() => setJiraOpen(false)}
        onSyncAll={(keys) => {
          setJiraSyncKeys(keys && keys.length > 0 ? keys : undefined);
          setJiraOpen(false);
          setJiraSyncOpen(true);
        }}
      />

      <BoardJiraSyncDialog
        board={board.data}
        open={jiraSyncOpen}
        initialKeys={jiraSyncKeys}
        onClose={() => {
          setJiraSyncOpen(false);
          setJiraSyncKeys(undefined);
        }}
      />

      <BoardSettingsDialog
        board={board.data}
        canArchive={board.data.my_role === "owner"}
        taskCounts={taskCounts}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onArchived={() => {
          setSettingsOpen(false);
          onBack();
        }}
      />
    </div>
  );
}

interface FilterOption {
  id: string;
  label: string;
  avatar?: string | null;
  /** Secondary text (e.g. email) shown in tooltips/sub-labels. */
  sub?: string | null;
}

function FilterBar({
  query,
  onQuery,
  memberOptions,
  agentOptions,
  labelOptions,
  assigneeFilter,
  agentFilter,
  labelFilter,
  onToggleAssignee,
  onToggleAgent,
  onToggleLabel,
  activeFilterCount,
  onClearFilters,
  onManageMembers,
}: {
  query: string;
  onQuery: (q: string) => void;
  memberOptions: FilterOption[];
  agentOptions: FilterOption[];
  labelOptions: FilterOption[];
  assigneeFilter: string[];
  agentFilter: string[];
  labelFilter: string[];
  onToggleAssignee: (id: string) => void;
  onToggleAgent: (id: string) => void;
  onToggleLabel: (id: string) => void;
  activeFilterCount: number;
  onClearFilters: () => void;
  onManageMembers: () => void;
}) {
  // Compact search field, then the clickable avatar stack (click a face to
  // filter by that assignee, Jira-style), then multi-select filter dropdowns.
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
      <div className="relative w-40 focus-within:w-52 transition-all duration-150">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          aria-label="Search board"
          className="h-8 w-full rounded border border-input bg-card pl-8 pr-2 text-sm text-foreground transition-colors hover:border-border-strong focus-visible:border-[#4C9AFF] focus-visible:outline-none"
        />
      </div>

      <AvatarGroup
        items={memberOptions.map((m) => ({
          id: m.id,
          name: m.label,
          src: m.avatar,
          title: m.sub && m.sub !== m.label ? `${m.label}\n${m.sub}` : m.label,
        }))}
        size={28}
        max={8}
        onItemClick={onToggleAssignee}
        activeIds={assigneeFilter}
        onClick={onManageMembers}
        emptyLabel="Members"
        className="px-1"
      />

      <MultiSelectFilter
        icon={<Users className="h-3.5 w-3.5" />}
        label="Assignee"
        options={memberOptions}
        selected={assigneeFilter}
        onToggle={onToggleAssignee}
        includeUnassigned
        footer={
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onManageMembers}>
              <Plus className="h-3.5 w-3.5" /> Manage members…
            </DropdownMenuItem>
          </>
        }
      />
      <MultiSelectFilter
        icon={<Bot className="h-3.5 w-3.5" />}
        label="Agent"
        options={agentOptions}
        selected={agentFilter}
        onToggle={onToggleAgent}
        includeUnassigned
        emptyHint="No agents staffed on this board."
      />
      <MultiSelectFilter
        icon={<Tag className="h-3.5 w-3.5" />}
        label="Label"
        options={labelOptions}
        selected={labelFilter}
        onToggle={onToggleLabel}
        emptyHint="No labels on this board yet."
      />

      {activeFilterCount > 0 && (
        <button
          type="button"
          onClick={onClearFilters}
          className="inline-flex h-8 items-center gap-1 rounded px-2 text-[13px] text-muted-foreground transition-colors hover:bg-surface-1 hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" /> Clear ({activeFilterCount})
        </button>
      )}
    </div>
  );
}

/**
 * Borderless Jira-style filter that opens a multi-select checklist dropdown.
 * Selecting an item toggles it without closing the menu.
 */
function MultiSelectFilter({
  icon,
  label,
  options,
  selected,
  onToggle,
  includeUnassigned,
  emptyHint,
  footer,
}: {
  icon: ReactNode;
  label: string;
  options: FilterOption[];
  selected: string[];
  onToggle: (id: string) => void;
  includeUnassigned?: boolean;
  emptyHint?: string;
  footer?: ReactNode;
}) {
  const count = selected.length;
  const allOptions: FilterOption[] = includeUnassigned
    ? [{ id: UNASSIGNED, label: "Unassigned" }, ...options]
    : options;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-[14px] transition-colors duration-100 hover:bg-surface-1",
            count > 0
              ? "bg-primary/10 text-primary"
              : "text-foreground active:bg-primary/10 active:text-primary",
          )}
        >
          {icon}
          {label}
          {count > 0 && (
            <span className="ml-0.5 rounded-full bg-primary px-1.5 text-[11px] font-semibold text-primary-foreground">
              {count}
            </span>
          )}
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 w-56 overflow-y-auto">
        {options.length === 0 && !includeUnassigned ? (
          <div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
            {emptyHint ?? "No options."}
          </div>
        ) : (
          allOptions.map((opt) => {
            const checked = selected.includes(opt.id);
            return (
              <DropdownMenuItem
                key={opt.id}
                onSelect={(e) => {
                  e.preventDefault();
                  onToggle(opt.id);
                }}
              >
                <span
                  className={cn(
                    "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                    checked
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border-strong",
                  )}
                >
                  {checked && <Check className="h-3 w-3" />}
                </span>
                {opt.id === UNASSIGNED ? (
                  <span className="italic text-muted-foreground">{opt.label}</span>
                ) : opt.avatar !== undefined ? (
                  <span className="flex min-w-0 items-center gap-2">
                    <JiraAvatar name={opt.label} src={opt.avatar} size={20} />
                    <span className="flex min-w-0 flex-col">
                      <span className="truncate leading-tight">{opt.label}</span>
                      {opt.sub && opt.sub !== opt.label && (
                        <span className="truncate text-[11px] leading-tight text-muted-foreground">
                          {opt.sub}
                        </span>
                      )}
                    </span>
                  </span>
                ) : (
                  <span className="truncate">{opt.label}</span>
                )}
              </DropdownMenuItem>
            );
          })
        )}
        {footer}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Current-Jira view tab: blue label + 2px blue underline when active. */
function PageTab({
  icon,
  label,
  active,
}: {
  icon: ReactNode;
  label: string;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={!active}
      className={cn(
        "-mb-px inline-flex h-9 items-center gap-1.5 border-b-2 px-3 text-[14px] transition-colors duration-100",
        active
          ? "border-primary font-medium text-primary"
          : "border-transparent text-muted-foreground hover:text-foreground disabled:cursor-not-allowed",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function RoleBadge({ role }: { role: string | null }) {
  if (!role) return null;
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[11px] font-medium capitalize",
        role === "owner"
          ? "bg-primary/10 text-primary"
          : "bg-secondary text-muted-foreground",
      )}
    >
      {role}
    </span>
  );
}
