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
  MoveTaskBody,
  PatchBoardBody,
  PatchCronBody,
  PatchTaskBody,
  RepoCreateBody,
  RepoUpdateBody,
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
  boardMembers: (id: string) => ["board-members", id] as const,
  agents: ["agents"] as const,
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
  taskComments: (taskId: string) => ["task-comments", taskId] as const,
  taskActivity: (taskId: string) => ["task-activity", taskId] as const,
  users: (q: string) => ["users", q] as const,
  repos: ["repos"] as const,
  boardRepos: (id: string) => ["board-repos", id] as const,
  taskRepos: (taskId: string) => ["task-repos", taskId] as const,
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
      }) =>
        client.assignBoardRepo(
          boardId,
          vars.repoId,
          vars.branchOverride,
          vars.allowPush,
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

export function usePrepareTaskRepos(taskId: string) {
  const { client } = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => client.prepareTaskRepos(taskId),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.taskRepos(taskId) }),
  });
}
