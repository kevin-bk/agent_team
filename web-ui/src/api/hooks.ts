import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useApi } from "./ApiProvider";
import type {
  AddMemberBody,
  CommentAttachment,
  CreateBoardBody,
  CreateCronBody,
  CreateTaskBody,
  JournalNoteBody,
  LoopResumeBody,
  PlanningAnswerBody,
  PlanningRunBody,
  PlanningStartBody,
  MoveTaskBody,
  PatchAutopilotBody,
  PatchBoardBody,
  PatchCronBody,
  PatchTaskBody,
  PatchTaskScheduleBody,
  RepoCreateBody,
  RepoUpdateBody,
  CredentialAccountCreateBody,
  CredentialAccountUpdateBody,
  BoardChannelUpsertBody,
  CommConnectionCreateBody,
  CommConnectionUpdateBody,
  TaskDTO,
} from "./types";

export const qk = {
  me: ["me"] as const,
  profiles: ["profiles"] as const,
  conversations: (profile: string) => ["conversations", profile] as const,
  conversation: (id: string) => ["conversation", id] as const,
  messages: (id: string) => ["messages", id] as const,
  cron: (profile: string) => ["cron", profile] as const,
  workspaceTree: (id: string, path: string) =>
    ["workspace-tree", id, path] as const,
  workspaceFile: (id: string, path: string) =>
    ["workspace-file", id, path] as const,
  boards: ["boards"] as const,
  board: (id: string) => ["board", id] as const,
  boardTasks: (id: string) => ["board-tasks", id] as const,
  boardFrictions: (id: string) => ["board-frictions", id] as const,
  boardMembers: (id: string) => ["board-members", id] as const,
  agents: ["agents"] as const,
  cliTargets: ["cli-targets"] as const,
  skills: ["skills"] as const,
  autopilot: (id: string) => ["autopilot", id] as const,
  autopilotSummary: (id: string) => ["autopilot-summary", id] as const,
  taskSchedule: (taskId: string) => ["task-schedule", taskId] as const,
  taskScheduleHistory: (taskId: string) =>
    ["task-schedule-history", taskId] as const,
  taskRuns: (taskId: string, agentId?: string) =>
    ["task-runs", taskId, agentId ?? "_all"] as const,
  runStats: (boardId: string, days: number, agentId?: string) =>
    ["run-stats", boardId, days, agentId ?? "_all"] as const,
  taskMessages: (taskId: string, agentId: string) =>
    ["task-messages", taskId, agentId] as const,
  taskAttempts: (taskId: string, agentId: string) =>
    ["task-attempts", taskId, agentId] as const,
  taskAttemptMessages: (taskId: string, agentId: string, convId: string) =>
    ["task-attempt-messages", taskId, agentId, convId] as const,
  taskFileTree: (taskId: string, path: string) =>
    ["task-file-tree", taskId, path] as const,
  taskFile: (taskId: string, path: string) =>
    ["task-file", taskId, path] as const,
  taskChanges: (taskId: string) => ["task-changes", taskId] as const,
  taskChangeDiff: (taskId: string, repo: string, path: string) =>
    ["task-change-diff", taskId, repo, path] as const,
  taskComments: (taskId: string) => ["task-comments", taskId] as const,
  taskActivity: (taskId: string) => ["task-activity", taskId] as const,
  taskLoop: (taskId: string) => ["task-loop", taskId] as const,
  taskPlanning: (taskId: string) => ["task-planning", taskId] as const,
  taskJournal: (taskId: string) => ["task-journal", taskId] as const,
  users: (q: string) => ["users", q] as const,
  repos: ["repos"] as const,
  credentialAccounts: ["credential-accounts"] as const,
  credentialProviders: ["credential-providers"] as const,
  boardRepos: (id: string) => ["board-repos", id] as const,
  taskRepos: (taskId: string) => ["task-repos", taskId] as const,
  taskRuntime: (taskId: string) => ["task-runtime", taskId] as const,
  commConnections: ["comm-connections"] as const,
  commUserLinks: (connId: string) => ["comm-user-links", connId] as const,
  boardChannel: (id: string) => ["board-channel", id] as const,
  boardDeliveries: (id: string) => ["board-deliveries", id] as const,
};

export function useMe() {
  const { client } = useApi();
  return useQuery({ queryKey: qk.me, queryFn: () => client.me() });
}

export function useProfiles() {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.profiles,
    queryFn: () => client.profiles(),
    staleTime: 60_000,
  });
}

export function useConversations(profile: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.conversations(profile ?? "_"),
    queryFn: () => client.listConversations(profile as string),
    enabled: !!profile,
  });
}

export function useConversation(convId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.conversation(convId ?? "_"),
    queryFn: () => client.getConversation(convId as string),
    enabled: !!convId,
  });
}

export function useMessages(convId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.messages(convId ?? "_"),
    queryFn: () => client.listMessages(convId as string),
    enabled: !!convId,
  });
}

export function useWorkspaceTree(
  convId: string | undefined,
  path = "",
) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.workspaceTree(convId ?? "_", path),
    queryFn: () => client.getWorkspaceTree(convId as string, path),
    enabled: !!convId,
  });
}

export function useWorkspaceFile(
  convId: string | undefined,
  path: string | undefined,
) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.workspaceFile(convId ?? "_", path ?? "_"),
    queryFn: () => client.getWorkspaceFile(convId as string, path as string),
    enabled: !!convId && !!path,
  });
}

export function useCreateConversation(profile: string | undefined) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (title: string) =>
      client.createConversation(profile as string, title),
    onSuccess: () => {
      if (profile) void qc.invalidateQueries({ queryKey: qk.conversations(profile) });
    },
  });
}

export function usePatchConversation(profile: string | undefined) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { convId: string; title?: string; status?: string }) =>
      client.patchConversation(vars.convId, {
        title: vars.title,
        status: vars.status,
      }),
    onSuccess: (_data, vars) => {
      if (profile) void qc.invalidateQueries({ queryKey: qk.conversations(profile) });
      void qc.invalidateQueries({ queryKey: qk.conversation(vars.convId) });
    },
  });
}

export function useDeleteConversation(profile: string | undefined) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (convId: string) => client.deleteConversation(convId),
    onSuccess: () => {
      if (profile) void qc.invalidateQueries({ queryKey: qk.conversations(profile) });
    },
  });
}

// ── boards + tasks (plan 16) ───────────────────────────────────────

export function useBoards() {
  const { client } = useApi();
  return useQuery({ queryKey: qk.boards, queryFn: () => client.listBoards() });
}

export function useBoard(boardId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.board(boardId ?? "_"),
    queryFn: () => client.getBoard(boardId as string),
    enabled: !!boardId,
  });
}

export function useBoardTasks(boardId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.boardTasks(boardId ?? "_"),
    queryFn: () => client.listBoardTasks(boardId as string),
    enabled: !!boardId,
    // Light multi-user freshness until board-SSE lands (plan 16 §9).
    refetchOnWindowFocus: true,
  });
}

export function useBoardFrictions(boardId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.boardFrictions(boardId ?? "_"),
    queryFn: () => client.listBoardFrictions(boardId as string),
    enabled: !!boardId,
    refetchOnWindowFocus: true,
  });
}

export function useBoardMembers(boardId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.boardMembers(boardId ?? "_"),
    queryFn: () => client.listBoardMembers(boardId as string),
    enabled: !!boardId,
  });
}

export function useCreateBoard() {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateBoardBody) => client.createBoard(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.boards }),
  });
}

export function usePreviewBoardTasksCsv(boardId: string) {
  const { client } = useApi();
  return useMutation({
    mutationFn: (file: File) => client.previewBoardTasksCsv(boardId, file),
  });
}

export function useImportBoardTasksCsv(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => client.importBoardTasksCsv(boardId, file),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) }),
  });
}

export function useUpdateBoard(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PatchBoardBody) => client.patchBoard(boardId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.boards });
      qc.invalidateQueries({ queryKey: qk.board(boardId) });
    },
  });
}

export function useAutopilot(boardId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.autopilot(boardId ?? "_"),
    queryFn: () => client.getAutopilot(boardId as string),
    enabled: !!boardId,
  });
}

export function useUpdateAutopilot(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PatchAutopilotBody) =>
      client.updateAutopilot(boardId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.autopilot(boardId) });
      qc.invalidateQueries({ queryKey: qk.autopilotSummary(boardId) });
    },
  });
}

export function useRouteAutopilot(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => client.routeAutopilot(boardId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) });
      qc.invalidateQueries({ queryKey: qk.autopilotSummary(boardId) });
    },
  });
}

export function useAutopilotSummary(boardId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.autopilotSummary(boardId ?? "_"),
    queryFn: () => client.getAutopilotSummary(boardId as string),
    enabled: !!boardId,
    // Cheap, frequently-changing status — keep it fresh while the panel is open.
    refetchInterval: 15_000,
  });
}

export function useTaskSchedule(taskId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskSchedule(taskId ?? "_"),
    queryFn: () => client.getTaskSchedule(taskId as string),
    enabled: !!taskId,
  });
}

export function useUpdateTaskSchedule(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PatchTaskScheduleBody) =>
      client.updateTaskSchedule(taskId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.taskSchedule(taskId) });
    },
  });
}

export function useTaskScheduleHistory(
  taskId: string | undefined,
  enabled = true,
) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskScheduleHistory(taskId ?? "_"),
    queryFn: () => client.getTaskScheduleHistory(taskId as string),
    enabled: !!taskId && enabled,
  });
}

export function useCreateTask(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateTaskBody) => client.createTask(body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) }),
  });
}

export function usePatchTask(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { taskId: string; body: PatchTaskBody }) =>
      client.patchTask(vars.taskId, vars.body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) }),
  });
}

export function useArchiveTask(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => client.archiveTask(taskId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) }),
  });
}

export function useSyncTaskFromJira(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { taskId: string; jiraKey?: string | null }) =>
      client.syncTaskFromJira(vars.taskId, vars.jiraKey),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) });
      void qc.invalidateQueries({ queryKey: qk.taskActivity(vars.taskId) });
    },
  });
}

export function useSyncBoardFromJira(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => client.syncBoardFromJira(boardId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) });
    },
  });
}

/**
 * Drag-and-drop move with an optimistic cache write + rollback on error.
 * The DnD layer already computed the target status + fractional position.
 */
export function useMoveTask(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  const key = qk.boardTasks(boardId);
  return useMutation({
    mutationFn: (vars: { taskId: string; body: MoveTaskBody }) =>
      client.moveTask(vars.taskId, vars.body),
    onMutate: async (vars) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<TaskDTO[]>(key);
      if (prev) {
        qc.setQueryData<TaskDTO[]>(
          key,
          prev.map((t) =>
            t.id === vars.taskId
              ? { ...t, status: vars.body.status, position: vars.body.position }
              : t,
          ),
        );
      }
      return { prev };
    },
    onError: (_err, _vars, context) => {
      if (context?.prev) qc.setQueryData(key, context.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });
}

export function useAddBoardMember(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AddMemberBody) => client.addBoardMember(boardId, body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.boardMembers(boardId) }),
  });
}

export function useRemoveBoardMember(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => client.removeBoardMember(boardId, userId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.boardMembers(boardId) }),
  });
}

// ── agents / mentions / runs (plan 16 Phase 2) ─────────────────────

export function useAgents() {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.agents,
    queryFn: () => client.listAgents(),
    staleTime: 30_000,
  });
}

export function useCliTargets() {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.cliTargets,
    queryFn: () => client.listCliTargets(),
    staleTime: 60_000,
  });
}

export function useSkills() {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.skills,
    queryFn: () => client.listSkills(),
    staleTime: 60_000,
  });
}

export function useTaskRuns(taskId: string | undefined, agentId?: string) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskRuns(taskId ?? "_", agentId),
    queryFn: () => client.listTaskRuns(taskId as string, agentId),
    enabled: !!taskId,
  });
}

export function useRunStats(
  boardId: string | undefined,
  days: number,
  agentId?: string,
) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.runStats(boardId ?? "_", days, agentId),
    queryFn: () => client.runStats(boardId as string, days, agentId),
    enabled: !!boardId,
  });
}

export function useTaskAttempts(
  taskId: string | undefined,
  agentId: string | undefined,
) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskAttempts(taskId ?? "_", agentId ?? "_"),
    queryFn: () =>
      client.listTaskAgentAttempts(taskId as string, agentId as string),
    enabled: !!taskId && !!agentId,
  });
}

export function useTaskAttemptMessages(
  taskId: string | undefined,
  agentId: string | undefined,
  convId: string | undefined,
) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskAttemptMessages(
      taskId ?? "_",
      agentId ?? "_",
      convId ?? "_",
    ),
    queryFn: () =>
      client.listTaskAttemptMessages(
        taskId as string,
        agentId as string,
        convId as string,
      ),
    enabled: !!taskId && !!agentId && !!convId,
  });
}

export function useTaskFileTree(taskId: string | undefined, path = "") {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskFileTree(taskId ?? "_", path),
    queryFn: () => client.getTaskWorkspaceTree(taskId as string, path),
    enabled: !!taskId,
  });
}

export function useTaskFile(
  taskId: string | undefined,
  path: string | undefined,
) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskFile(taskId ?? "_", path ?? "_"),
    queryFn: () => client.getTaskWorkspaceFile(taskId as string, path as string),
    enabled: !!taskId && !!path,
  });
}

export function useTaskComments(taskId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskComments(taskId ?? "_"),
    queryFn: () => client.listTaskComments(taskId as string),
    enabled: !!taskId,
  });
}

/**
 * Git changeset for a task's repo working copies (the "Changes" view). Reflects
 * on-disk truth vs the base branch, so it spans every agent/run/CLI. Callers
 * (e.g. the cockpit) invalidate `qk.taskChanges(id)` on run SSE / file writes.
 */
export function useTaskChanges(taskId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskChanges(taskId ?? "_"),
    queryFn: () => client.getTaskChanges(taskId as string),
    enabled: !!taskId,
  });
}

/**
 * Old/new content for one changed file. Lazy: pass `enabled` so the diff is only
 * fetched when its card is expanded.
 */
export function useTaskChangeDiff(args: {
  taskId: string | undefined;
  repo: string;
  path: string;
  enabled?: boolean;
}) {
  const { taskId, repo, path, enabled = true } = args;
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskChangeDiff(taskId ?? "_", repo, path),
    queryFn: () => client.getTaskChangeDiff(taskId as string, repo, path),
    enabled: enabled && !!taskId && !!repo && !!path,
    staleTime: 5 * 60_000,
  });
}

export function useUsers(q = "") {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.users(q),
    queryFn: () => client.listUsers(q || undefined),
    staleTime: 60_000,
  });
}

export function useTaskActivity(taskId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskActivity(taskId ?? "_"),
    queryFn: () => client.listTaskActivity(taskId as string),
    enabled: !!taskId,
  });
}

export function useCreateTaskComment(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      body: string;
      attachments?: CommentAttachment[];
      visibleToAgents?: boolean;
    }) =>
      client.createTaskComment(
        taskId,
        vars.body,
        vars.attachments ?? [],
        vars.visibleToAgents ?? true,
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.taskComments(taskId) });
    },
  });
}

/**
 * Object URL for a task-workspace file, fetched with the bearer token (so it
 * works in `<img>`/`<a>`). Cached for the session; the blob URL is intentionally
 * not revoked (bounded, released on reload).
 */
export function useTaskFileBlobUrl(taskId: string, path: string) {
  const { client } = useApi();
  return useQuery({
    queryKey: ["taskFileBlob", taskId, path],
    queryFn: async () => {
      const blob = await client.taskFileBlob(taskId, path);
      return URL.createObjectURL(blob);
    },
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
    enabled: !!taskId && !!path,
  });
}

/**
 * Object URL for an authenticated `/api` image URL. Mirrors
 * {@link useTaskFileBlobUrl}: the bytes are fetched with the bearer token (so
 * it never depends on cookie auth) and cached for the session. `enabled` lets
 * the caller defer the fetch until the image scrolls into view.
 */
export function useImageBlobUrl(url: string | undefined, enabled = true) {
  const { client } = useApi();
  return useQuery({
    queryKey: ["imageBlob", url],
    queryFn: async () => URL.createObjectURL(await client.fetchBlob(url as string)),
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
    enabled: enabled && !!url,
  });
}

export function useWriteTaskFile(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { path: string; content: string }) =>
      client.writeTaskWorkspaceFile(taskId, vars.path, vars.content),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.taskFile(taskId, vars.path) });
      void qc.invalidateQueries({ queryKey: ["task-file-tree", taskId] });
    },
  });
}

export function useDeleteTaskFile(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (path: string) => client.deleteTaskWorkspaceFile(taskId, path),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["task-file-tree", taskId] });
    },
  });
}

export function useUpdateTaskComment(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      commentId: string;
      body?: string;
      visibleToAgents?: boolean;
    }) =>
      client.updateTaskComment(taskId, vars.commentId, {
        ...(vars.body !== undefined && { body: vars.body }),
        ...(vars.visibleToAgents !== undefined && {
          visible_to_agents: vars.visibleToAgents,
        }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.taskComments(taskId) });
    },
  });
}

export function useDeleteTaskComment(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (commentId: string) =>
      client.deleteTaskComment(taskId, commentId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.taskComments(taskId) });
    },
  });
}

// ── autonomous loop (generator + evaluator) ────────────────────────

export function useTaskLoop(taskId: string | undefined, enabled = true) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskLoop(taskId ?? "_"),
    queryFn: () => client.getTaskLoop(taskId as string),
    enabled: !!taskId && enabled,
    // While a loop is live (or the planner is drafting), poll so the active run
    // / attempts / verdicts (and therefore the embedded transcript that follows
    // the running role) refresh as the goal advances between planner, generator
    // and critic.
    refetchInterval: (query) =>
      query.state.data?.is_running ||
      query.state.data?.loop_state === "planning"
        ? 4000
        : false,
  });
}

export function useCancelTaskLoop(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => client.cancelTaskLoop(taskId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.taskLoop(taskId) }),
  });
}

export function useResumeTaskLoop(boardId: string, taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LoopResumeBody = {}) =>
      client.resumeTaskLoop(taskId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.taskLoop(taskId) });
      void qc.invalidateQueries({ queryKey: qk.taskPlanning(taskId) });
      void qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) });
    },
  });
}

export function useAckTaskLoop(boardId: string, taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => client.ackTaskLoop(taskId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.taskLoop(taskId) });
      void qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) });
    },
  });
}

export function useTaskPlanning(taskId: string | undefined, enabled = true) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskPlanning(taskId ?? "_"),
    queryFn: () => client.getTaskPlanning(taskId as string),
    enabled: !!taskId && enabled,
    // Poll while the planner is drafting so artifacts appear as they land.
    refetchInterval: (query) =>
      query.state.data?.is_planning ? 3000 : false,
  });
}

export function useStartTaskPlanning(boardId: string, taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PlanningStartBody) =>
      client.startTaskPlanning(taskId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.taskPlanning(taskId) });
      void qc.invalidateQueries({ queryKey: qk.taskLoop(taskId) });
      void qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) });
    },
  });
}

export function useApproveTaskPlanning(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => client.approveTaskPlanning(taskId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.taskPlanning(taskId) }),
  });
}

export function useRequestTaskPlanningChanges(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (feedback?: string) =>
      client.requestTaskPlanningChanges(taskId, feedback),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.taskPlanning(taskId) }),
  });
}

export function useApproveAndRunTaskPlanning(boardId: string, taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PlanningRunBody) =>
      client.approveAndRunTaskPlanning(taskId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.taskPlanning(taskId) });
      void qc.invalidateQueries({ queryKey: qk.taskLoop(taskId) });
      void qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) });
    },
  });
}

export function useEditTaskPlanningArtifact(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { name: string; content: string; etag?: string | null }) =>
      client.editTaskPlanningArtifact(taskId, v.name, v.content, v.etag),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.taskPlanning(taskId) }),
  });
}

export function useAnswerTaskPlanning(boardId: string, taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PlanningAnswerBody) =>
      client.answerTaskPlanning(taskId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.taskPlanning(taskId) });
      void qc.invalidateQueries({ queryKey: qk.taskLoop(taskId) });
      void qc.invalidateQueries({ queryKey: qk.boardTasks(boardId) });
    },
  });
}

export function useTaskJournal(taskId: string | undefined, enabled = true) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskJournal(taskId ?? "_"),
    queryFn: () => client.getTaskJournal(taskId as string, { limit: 500 }),
    enabled: !!taskId && enabled,
  });
}

export function useAddTaskJournalNote(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JournalNoteBody) => client.addTaskJournalNote(taskId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.taskJournal(taskId) }),
  });
}

export function useResetTaskThread(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (agentId: string) =>
      client.resetTaskAgentThread(taskId, agentId),
    onSuccess: (_data, agentId) => {
      void qc.invalidateQueries({ queryKey: qk.taskAttempts(taskId, agentId) });
      void qc.invalidateQueries({ queryKey: qk.taskMessages(taskId, agentId) });
    },
  });
}

export function useCron(profile: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.cron(profile ?? "_"),
    queryFn: () => client.listCron(profile as string),
    enabled: !!profile,
  });
}

export function useCronMutations(profile: string | undefined) {
  const { client } = useApi();
  const qc = useQueryClient();
  const invalidate = () => {
    if (profile) void qc.invalidateQueries({ queryKey: qk.cron(profile) });
  };
  return {
    create: useMutation({
      mutationFn: (body: CreateCronBody) => client.createCron(body),
      onSuccess: invalidate,
    }),
    patch: useMutation({
      mutationFn: (vars: { jobId: string; body: PatchCronBody }) =>
        client.patchCron(profile as string, vars.jobId, vars.body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (jobId: string) =>
        client.deleteCron(profile as string, jobId),
      onSuccess: invalidate,
    }),
    runNow: useMutation({
      mutationFn: (jobId: string) =>
        client.runCronNow(profile as string, jobId),
    }),
  };
}

// ── code repositories ──────────────────────────────────────────────

export function useRepos() {
  const { client } = useApi();
  return useQuery({ queryKey: qk.repos, queryFn: () => client.listRepos() });
}

export function useRepoMutations() {
  const { client } = useApi();
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: qk.repos });
  return {
    create: useMutation({
      mutationFn: (body: RepoCreateBody) => client.createRepo(body),
      onSuccess: invalidate,
    }),
    patch: useMutation({
      mutationFn: (vars: { repoId: string; body: RepoUpdateBody }) =>
        client.patchRepo(vars.repoId, vars.body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (repoId: string) => client.deleteRepo(repoId),
      onSuccess: invalidate,
    }),
    clone: useMutation({
      mutationFn: (repoId: string) => client.cloneRepo(repoId),
      onSuccess: invalidate,
    }),
    pull: useMutation({
      mutationFn: (repoId: string) => client.pullRepo(repoId),
      onSuccess: invalidate,
    }),
  };
}

// ── credential accounts (admin) ────────────────────────────────────

export function useCredentialProviders() {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.credentialProviders,
    queryFn: () => client.listCredentialProviders(),
    staleTime: 5 * 60_000,
  });
}

export function useCredentialAccounts() {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.credentialAccounts,
    queryFn: () => client.listCredentialAccounts(),
  });
}

export function useCredentialAccountMutations() {
  const { client } = useApi();
  const qc = useQueryClient();
  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: qk.credentialAccounts });
  return {
    create: useMutation({
      mutationFn: (body: CredentialAccountCreateBody) =>
        client.createCredentialAccount(body),
      onSuccess: invalidate,
    }),
    patch: useMutation({
      mutationFn: (vars: {
        accountId: string;
        body: CredentialAccountUpdateBody;
      }) => client.patchCredentialAccount(vars.accountId, vars.body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (accountId: string) =>
        client.deleteCredentialAccount(accountId),
      onSuccess: invalidate,
    }),
  };
}

export function useBoardRepos(boardId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.boardRepos(boardId ?? "_"),
    queryFn: () => client.listBoardRepos(boardId as string),
    enabled: !!boardId,
  });
}

export function useBoardRepoMutations(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: qk.boardRepos(boardId) });
  return {
    assign: useMutation({
      mutationFn: (vars: {
        repoId: string;
        branchOverride?: string | null;
        allowPush?: boolean;
        isWiki?: boolean;
      }) =>
        client.assignBoardRepo(
          boardId,
          vars.repoId,
          vars.branchOverride,
          vars.allowPush,
          vars.isWiki,
        ),
      onSuccess: invalidate,
    }),
    unassign: useMutation({
      mutationFn: (repoId: string) => client.unassignBoardRepo(boardId, repoId),
      onSuccess: invalidate,
    }),
  };
}

export function useTaskRepos(taskId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskRepos(taskId ?? "_"),
    queryFn: () => client.listTaskRepos(taskId as string),
    enabled: !!taskId,
  });
}

export function useTaskRuntime(taskId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.taskRuntime(taskId ?? "_"),
    queryFn: () => client.getTaskRuntime(taskId as string),
    enabled: !!taskId,
    refetchInterval: 15000,
  });
}

export function useControlTaskRuntime(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: "pause" | "kill") =>
      client.controlTaskRuntime(taskId, action),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: qk.taskRuntime(taskId) }),
  });
}

export function usePrepareTaskRepos(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => client.prepareTaskRepos(taskId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.taskRepos(taskId) }),
  });
}

export function useResetTaskRepos(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => client.resetTaskRepos(taskId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.taskRepos(taskId) }),
  });
}

// ── communication gateway ──────────────────────────────────────────────────

export function useCommEventTypes() {
  const { client } = useApi();
  return useQuery({
    queryKey: ["comm-event-types"],
    queryFn: () => client.commEventTypes(),
  });
}

export function useCommProviders() {
  const { client } = useApi();
  return useQuery({
    queryKey: ["comm-providers"],
    queryFn: () => client.commProviders(),
    staleTime: 5 * 60 * 1000,
  });
}

export function useCommConnections() {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.commConnections,
    queryFn: () => client.listCommConnections(),
  });
}

export function useCommConnectionMutations() {
  const { client } = useApi();
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: qk.commConnections });
  return {
    create: useMutation({
      mutationFn: (body: CommConnectionCreateBody) => client.createCommConnection(body),
      onSuccess: invalidate,
    }),
    patch: useMutation({
      mutationFn: (vars: { connectionId: string; body: CommConnectionUpdateBody }) =>
        client.patchCommConnection(vars.connectionId, vars.body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (connectionId: string) => client.deleteCommConnection(connectionId),
      onSuccess: invalidate,
    }),
  };
}

export function useCommUserLinks(connectionId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.commUserLinks(connectionId ?? "_"),
    queryFn: () => client.listCommUserLinks(connectionId as string),
    enabled: !!connectionId,
  });
}

export function useCommUserLinkMutations(connectionId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: qk.commUserLinks(connectionId) });
  return {
    upsert: useMutation({
      mutationFn: (body: { user_id: string; mm_username?: string | null }) =>
        client.upsertCommUserLink(connectionId, body),
      onSuccess: invalidate,
    }),
    autoMatch: useMutation({
      mutationFn: () => client.autoMatchCommUserLinks(connectionId),
      onSuccess: invalidate,
    }),
  };
}

export function useBoardChannel(boardId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.boardChannel(boardId ?? "_"),
    queryFn: () => client.getBoardChannel(boardId as string),
    enabled: !!boardId,
  });
}

export function useBoardChannelMutations(boardId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: qk.boardChannel(boardId) });
    void qc.invalidateQueries({ queryKey: qk.boardDeliveries(boardId) });
  };
  return {
    save: useMutation({
      mutationFn: (body: BoardChannelUpsertBody) => client.putBoardChannel(boardId, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: () => client.deleteBoardChannel(boardId),
      onSuccess: invalidate,
    }),
    test: useMutation({
      mutationFn: () => client.testBoardChannel(boardId),
    }),
  };
}

export function useBoardDeliveries(boardId: string | undefined) {
  const { client } = useApi();
  return useQuery({
    queryKey: qk.boardDeliveries(boardId ?? "_"),
    queryFn: () => client.listBoardDeliveries(boardId as string),
    enabled: !!boardId,
  });
}
