import {
  ArrowLeft,
  Bot,
  Calendar,
  ChevronDown,
  Columns3,
  GitBranch,
  List,
  Plus,
  Search,
  Settings,
  Tag,
  Users,
} from "@/components/icons";
import { type ReactNode, useCallback, useMemo, useRef, useState } from "react";
import { useBoard, useBoardMembers, useBoardTasks, useMoveTask } from "@/api/hooks";
import type { BoardMemberDTO, TaskDTO } from "@/api/types";
import { AvatarGroup, Breadcrumbs, JiraIcon } from "@/components/jira";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { BoardEventsProvider } from "./BoardEventsContext";
import { BoardAgentsDialog } from "./BoardAgentsDialog";
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
      <BoardViewInner {...props} />
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
  const tasksQuery = useBoardTasks(boardId);
  const move = useMoveTask(boardId);

  const members = useBoardMembers(boardId);
  const [dialog, setDialog] = useState<
    { mode: "create"; status: string } | { mode: "edit"; task: TaskDTO } | null
  >(null);
  const [membersOpen, setMembersOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [reposOpen, setReposOpen] = useState(false);
  const [jiraOpen, setJiraOpen] = useState(false);
  const [jiraSyncOpen, setJiraSyncOpen] = useState(false);
  const [query, setQuery] = useState("");

  const tasks = useMemo(() => tasksQuery.data ?? [], [tasksQuery.data]);
  const visibleTasks = useMemo(
    () => tasks.filter((t) => matchesQuery(t, query)),
    [tasks, query],
  );
  const membersById = useMemo(() => {
    const map = new Map<string, BoardMemberDTO>();
    for (const m of members.data ?? []) map.set(m.user_id, m);
    return map;
  }, [members.data]);
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
                aria-label="Board repositories"
                onClick={() => setReposOpen(true)}
              >
                <GitBranch className="h-4 w-4" /> Code
              </Button>
            )}
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
          members={members.data ?? []}
          onMembersClick={() => setMembersOpen(true)}
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

      <BoardReposDialog
        boardId={boardId}
        open={reposOpen}
        onClose={() => setReposOpen(false)}
      />

      <BoardJiraDialog
        board={board.data}
        open={jiraOpen}
        onClose={() => setJiraOpen(false)}
        onSyncAll={() => {
          setJiraOpen(false);
          setJiraSyncOpen(true);
        }}
      />

      <BoardJiraSyncDialog
        board={board.data}
        open={jiraSyncOpen}
        onClose={() => setJiraSyncOpen(false)}
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

function FilterBar({
  query,
  onQuery,
  members,
  onMembersClick,
}: {
  query: string;
  onQuery: (q: string) => void;
  members: BoardMemberDTO[];
  onMembersClick: () => void;
}) {
  // Demo layout: compact search field, then the avatar stack (lift on
  // hover), then plain borderless text-button filters — all on the page,
  // no bar chrome.
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
        items={members.map((m) => ({
          id: m.user_id,
          name: m.display_name || m.email || m.user_id,
          src: m.avatar_url,
        }))}
        size={28}
        max={6}
        onClick={onMembersClick}
        emptyLabel="Members"
        className="px-1"
      />

      <FilterButton icon={<Users className="h-3.5 w-3.5" />} label="Assignee" />
      <FilterButton icon={<Bot className="h-3.5 w-3.5" />} label="Agent" />
      <FilterButton icon={<Tag className="h-3.5 w-3.5" />} label="Label" />
    </div>
  );
}

/** Borderless "btn-empty" filter (jira-clone style): plain text, gray hover. */
function FilterButton({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <button
      type="button"
      className="inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-[14px] text-foreground transition-colors duration-100 hover:bg-surface-1 active:bg-primary/10 active:text-primary"
    >
      {icon}
      {label}
      <ChevronDown className="h-3 w-3 text-muted-foreground" />
    </button>
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
