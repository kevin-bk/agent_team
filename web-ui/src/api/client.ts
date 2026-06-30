import {
  ApiError,
  type AddMemberBody,
  type AgentDTO,
  type AttachmentDTO,
  type AttemptDTO,
  type AutopilotDTO,
  type AutopilotSummaryDTO,
  type BoardDTO,
  type BoardFrictionDTO,
  type BoardMemberDTO,
  type CliTargetDTO,
  type SkillPackDTO,
  type ConversationDetail,
  type ConversationSummary,
  type CreateBoardBody,
  type CreateCronBody,
  type CreateTaskBody,
  type CronJob,
  type CsvImportPreview,
  type CsvImportResult,
  type JiraBatchResult,
  type JiraPreviewResponse,
  type JournalEntryDTO,
  type JournalFilters,
  type JournalNoteBody,
  type LoopInfoDTO,
  type LoopResumeBody,
  type PlanningAnswerBody,
  type PlanningInfoDTO,
  type PlanningRunBody,
  type PlanningStartBody,
  type Me,
  type MentionBody,
  type MentionResponse,
  type MessageDTO,
  type MoveTaskBody,
  type PatchAutopilotBody,
  type PatchBoardBody,
  type PatchCronBody,
  type PatchTaskBody,
  type PatchTaskScheduleBody,
  type TaskScheduleDTO,
  type TaskScheduleHistoryItem,
  type PlanDTO,
  type ProfileDTO,
  type BoardRepoDTO,
  type BoardReposResponse,
  type BoardChannelDTO,
  type BoardChannelResponse,
  type BoardChannelUpsertBody,
  type CommConnectionDTO,
  type CommConnectionCreateBody,
  type CommConnectionUpdateBody,
  type CommProviderDescriptor,
  type CommDeliveryDTO,
  type CommTestSendResult,
  type CommUserLinkDTO,
  type RepoCreateBody,
  type RepoDTO,
  type RepoStatusDTO,
  type RepoSyncResult,
  type RepoUpdateBody,
  type TaskRepoDir,
  type SearchHit,
  type TaskActivityDTO,
  type CommentAttachment,
  type TaskCommentDTO,
  type UserDTO,
  type TaskDTO,
  type RunStatsResponse,
  type TaskRunDTO,
  type WorkspaceFileResponse,
  type WorkspaceTreeResponse,
  type TaskChangesResponse,
  type TaskFileDiff,
} from "./types";
import { apiUrl } from "./config";

export type TokenGetter = () => Promise<string | null>;

/**
 * Thin typed REST wrapper. Auth is a Clerk bearer token fetched fresh per
 * request (Clerk rotates short-lived JWTs). SSE is handled separately in
 * `sse.ts` because EventSource can't set headers.
 */
export class ApiClient {
  constructor(private readonly getToken: TokenGetter) {}

  private async request<T>(
    path: string,
    init: RequestInit = {},
  ): Promise<T> {
    const token = await this.getToken();
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body && !(init.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const res = await fetch(apiUrl(path), { ...init, headers });
    if (!res.ok) {
      throw new ApiError(res.status, await extractError(res));
    }
    if (res.status === 204) return undefined as T;
    const ct = res.headers.get("content-type") ?? "";
    if (ct.includes("application/json")) return (await res.json()) as T;
    return (await res.text()) as unknown as T;
  }

  // ── identity / profiles ──────────────────────────────────────────
  me() {
    return this.request<Me>("/api/me");
  }
  profiles() {
    return this.request<ProfileDTO[]>("/api/profiles");
  }

  // ── conversations ────────────────────────────────────────────────
  listConversations(profile: string, status = "active") {
    const q = new URLSearchParams({ profile, status, limit: "100" });
    return this.request<ConversationSummary[]>(`/api/conversations?${q}`);
  }
  createConversation(profile_name: string, title = "") {
    return this.request<ConversationDetail["conversation"]>(
      "/api/conversations",
      { method: "POST", body: JSON.stringify({ profile_name, title }) },
    );
  }
  getConversation(convId: string) {
    return this.request<ConversationDetail>(`/api/conversations/${convId}`);
  }
  patchConversation(
    convId: string,
    body: { title?: string; status?: string },
  ) {
    return this.request<ConversationDetail["conversation"]>(
      `/api/conversations/${convId}`,
      { method: "PATCH", body: JSON.stringify(body) },
    );
  }
  deleteConversation(convId: string) {
    return this.request<{ ok: boolean }>(`/api/conversations/${convId}`, {
      method: "DELETE",
    });
  }
  listMessages(convId: string) {
    return this.request<MessageDTO[]>(
      `/api/conversations/${convId}/messages?limit=500`,
    );
  }
  getTodos(convId: string) {
    return this.request<PlanDTO>(`/api/conversations/${convId}/todos`);
  }
  search(profile: string, q: string) {
    const qs = new URLSearchParams({ profile, q });
    return this.request<SearchHit[]>(`/api/conversations/search?${qs}`);
  }
  postMessage(
    convId: string,
    content: string,
    mode: "queue" | "interrupt" | "steer",
  ) {
    return this.request<{ ok: boolean; item_id: string }>(
      `/api/conversations/${convId}/messages`,
      { method: "POST", body: JSON.stringify({ content, mode }) },
    );
  }
  cancelRun(convId: string) {
    return this.request<{ ok: boolean }>(
      `/api/conversations/${convId}/cancel`,
      { method: "POST" },
    );
  }
  /** Full output of one tool call, loaded on demand when a card is expanded. */
  getToolOutput(runId: string, toolId: string) {
    return this.request<{ content: string; is_error: boolean }>(
      `/api/runs/${runId}/tools/${encodeURIComponent(toolId)}/output`,
    );
  }
  uploadConversationAttachments(convId: string, files: File[]) {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    return this.request<AttachmentDTO[]>(
      `/api/conversations/${convId}/attachments`,
      { method: "POST", body: fd },
    );
  }

  // ── workspace files (design 26) ──────────────────────────────────
  getWorkspaceTree(convId: string, path = "", depth = 1) {
    const q = new URLSearchParams({ path, depth: String(depth) });
    return this.request<WorkspaceTreeResponse>(
      `/api/conversations/${convId}/files/tree?${q}`,
    );
  }
  getWorkspaceFile(convId: string, path: string) {
    const q = new URLSearchParams({ path });
    return this.request<WorkspaceFileResponse>(
      `/api/conversations/${convId}/files?${q}`,
    );
  }
  workspaceFileRawUrl(convId: string, path: string) {
    const q = new URLSearchParams({ path });
    return apiUrl(`/api/conversations/${convId}/files/raw?${q}`);
  }

  // ── cron ─────────────────────────────────────────────────────────
  listCron(profile: string) {
    return this.request<CronJob[]>(
      `/api/cron?${new URLSearchParams({ profile })}`,
    );
  }
  createCron(body: CreateCronBody) {
    return this.request<CronJob>("/api/cron", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  patchCron(profile: string, jobId: string, body: PatchCronBody) {
    return this.request<CronJob>(
      `/api/cron/${jobId}?${new URLSearchParams({ profile })}`,
      { method: "PATCH", body: JSON.stringify(body) },
    );
  }
  deleteCron(profile: string, jobId: string) {
    return this.request<{ ok: boolean }>(
      `/api/cron/${jobId}?${new URLSearchParams({ profile })}`,
      { method: "DELETE" },
    );
  }
  runCronNow(profile: string, jobId: string) {
    return this.request<{ ok: boolean }>(
      `/api/cron/${jobId}/run?${new URLSearchParams({ profile })}`,
      { method: "POST" },
    );
  }

  // ── boards (plan 16) ─────────────────────────────────────────────
  listBoards() {
    return this.request<BoardDTO[]>("/api/boards");
  }
  createBoard(body: CreateBoardBody) {
    return this.request<BoardDTO>("/api/boards", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  getBoard(boardId: string) {
    return this.request<BoardDTO>(`/api/boards/${boardId}`);
  }
  patchBoard(boardId: string, body: PatchBoardBody) {
    return this.request<BoardDTO>(`/api/boards/${boardId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }
  listBoardTasks(
    boardId: string,
    filters: { status?: string; assignee?: string; q?: string } = {},
  ) {
    const q = new URLSearchParams();
    if (filters.status) q.set("status", filters.status);
    if (filters.assignee) q.set("assignee", filters.assignee);
    if (filters.q) q.set("q", filters.q);
    const qs = q.toString();
    return this.request<TaskDTO[]>(
      `/api/boards/${boardId}/tasks${qs ? `?${qs}` : ""}`,
    );
  }
  listBoardMembers(boardId: string) {
    return this.request<BoardMemberDTO[]>(`/api/boards/${boardId}/members`);
  }
  addBoardMember(boardId: string, body: AddMemberBody) {
    return this.request<BoardMemberDTO>(`/api/boards/${boardId}/members`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  removeBoardMember(boardId: string, userId: string) {
    return this.request<{ ok: boolean }>(
      `/api/boards/${boardId}/members/${userId}`,
      { method: "DELETE" },
    );
  }

  // ── tasks (plan 16) ──────────────────────────────────────────────
  createTask(body: CreateTaskBody) {
    return this.request<TaskDTO>("/api/tasks", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  getTask(taskId: string) {
    return this.request<TaskDTO>(`/api/tasks/${taskId}`);
  }
  patchTask(taskId: string, body: PatchTaskBody) {
    return this.request<TaskDTO>(`/api/tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }
  moveTask(taskId: string, body: MoveTaskBody) {
    return this.request<TaskDTO>(`/api/tasks/${taskId}/move`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  archiveTask(taskId: string) {
    return this.request<{ ok: boolean }>(`/api/tasks/${taskId}`, {
      method: "DELETE",
    });
  }
  syncTaskFromJira(taskId: string, jiraKey?: string | null) {
    return this.request<TaskDTO>(`/api/tasks/${taskId}/jira/sync`, {
      method: "POST",
      body: JSON.stringify({ jira_key: jiraKey ?? null }),
    });
  }
  syncBoardFromJira(boardId: string) {
    return this.request<JiraBatchResult>(`/api/boards/${boardId}/jira/sync`, {
      method: "POST",
    });
  }
  previewBoardJiraSync(boardId: string, jiraKeys?: string[]) {
    const hasKeys = jiraKeys && jiraKeys.length > 0;
    return this.request<JiraPreviewResponse>(
      `/api/boards/${boardId}/jira/sync/preview`,
      {
        method: "POST",
        ...(hasKeys ? { body: JSON.stringify({ jira_keys: jiraKeys }) } : {}),
      },
    );
  }
  importIssueFromJira(boardId: string, jiraKey: string) {
    return this.request<TaskDTO>(`/api/boards/${boardId}/jira/import`, {
      method: "POST",
      body: JSON.stringify({ jira_key: jiraKey }),
    });
  }

  // ── CSV import / export ──────────────────────────────────────────
  async exportBoardTasksCsv(
    boardId: string,
    includeArchived = false,
  ): Promise<string> {
    const q = new URLSearchParams({
      include_archived: String(includeArchived),
    });
    return this.request<string>(
      `/api/boards/${boardId}/tasks/export.csv?${q}`,
    );
  }
  previewBoardTasksCsv(boardId: string, file: File) {
    const fd = new FormData();
    fd.append("file", file);
    return this.request<CsvImportPreview>(
      `/api/boards/${boardId}/tasks/import/preview`,
      { method: "POST", body: fd },
    );
  }
  importBoardTasksCsv(boardId: string, file: File) {
    const fd = new FormData();
    fd.append("file", file);
    return this.request<CsvImportResult>(
      `/api/boards/${boardId}/tasks/import`,
      { method: "POST", body: fd },
    );
  }

  // ── agents / mentions / runs (plan 16 Phase 2) ───────────────────
  listAgents() {
    return this.request<AgentDTO[]>("/api/agents");
  }
  listCliTargets() {
    return this.request<CliTargetDTO[]>("/api/cli-targets");
  }
  listSkills() {
    return this.request<SkillPackDTO[]>("/api/skills");
  }
  getAutopilot(boardId: string) {
    return this.request<AutopilotDTO>(`/api/boards/${boardId}/autopilot`);
  }
  updateAutopilot(boardId: string, body: PatchAutopilotBody) {
    return this.request<AutopilotDTO>(`/api/boards/${boardId}/autopilot`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }
  getAutopilotSummary(boardId: string) {
    return this.request<AutopilotSummaryDTO>(
      `/api/boards/${boardId}/autopilot/summary`,
    );
  }
  routeAutopilot(boardId: string) {
    return this.request<{ assigned: number }>(
      `/api/boards/${boardId}/autopilot/route`,
      { method: "POST" },
    );
  }
  getTaskSchedule(taskId: string) {
    return this.request<TaskScheduleDTO>(`/api/tasks/${taskId}/schedule`);
  }
  updateTaskSchedule(taskId: string, body: PatchTaskScheduleBody) {
    return this.request<TaskScheduleDTO>(`/api/tasks/${taskId}/schedule`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }
  getTaskScheduleHistory(taskId: string) {
    return this.request<TaskScheduleHistoryItem[]>(
      `/api/tasks/${taskId}/schedule/history`,
    );
  }
  mentionAgent(taskId: string, body: MentionBody) {
    return this.request<MentionResponse>(`/api/tasks/${taskId}/mentions`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  // ── autonomous loop (generator + evaluator) ──────────────────────
  getTaskLoop(taskId: string) {
    return this.request<LoopInfoDTO>(`/api/tasks/${taskId}/loop`);
  }
  cancelTaskLoop(taskId: string) {
    return this.request<{ ok: boolean; task_id: string }>(
      `/api/tasks/${taskId}/loop/cancel`,
      { method: "POST" },
    );
  }
  resumeTaskLoop(taskId: string, body: LoopResumeBody = {}) {
    return this.request<{ ok: boolean; task_id: string }>(
      `/api/tasks/${taskId}/loop/resume`,
      { method: "POST", body: JSON.stringify(body) },
    );
  }
  ackTaskLoop(taskId: string) {
    return this.request<{ ok: boolean; task_id: string }>(
      `/api/tasks/${taskId}/loop/ack`,
      { method: "POST" },
    );
  }

  // ── strict planning (draft → approve → run) ──────────────────────
  getTaskPlanning(taskId: string) {
    return this.request<PlanningInfoDTO>(`/api/tasks/${taskId}/planning`);
  }
  startTaskPlanning(taskId: string, body: PlanningStartBody) {
    return this.request<{ ok: boolean; task_id: string }>(
      `/api/tasks/${taskId}/planning/start`,
      { method: "POST", body: JSON.stringify(body) },
    );
  }
  approveTaskPlanning(taskId: string) {
    return this.request<PlanningInfoDTO>(
      `/api/tasks/${taskId}/planning/approve`,
      { method: "POST" },
    );
  }
  requestTaskPlanningChanges(taskId: string, feedback?: string) {
    return this.request<{ ok: boolean; task_id: string }>(
      `/api/tasks/${taskId}/planning/request-changes`,
      { method: "POST", body: JSON.stringify({ objective: feedback ?? null }) },
    );
  }
  approveAndRunTaskPlanning(taskId: string, body: PlanningRunBody) {
    return this.request<{ ok: boolean; task_id: string }>(
      `/api/tasks/${taskId}/planning/approve-and-run`,
      { method: "POST", body: JSON.stringify(body) },
    );
  }
  answerTaskPlanning(taskId: string, body: PlanningAnswerBody) {
    return this.request<{ ok: boolean; task_id: string; resumed: string }>(
      `/api/tasks/${taskId}/planning/answer`,
      { method: "POST", body: JSON.stringify(body) },
    );
  }
  // ── task journal (sổ cái) ────────────────────────────────────────
  getTaskJournal(taskId: string, filters: JournalFilters = {}) {
    const q = new URLSearchParams();
    if (filters.type) q.set("type", filters.type);
    if (filters.phase) q.set("phase", filters.phase);
    if (filters.severity) q.set("severity", filters.severity);
    if (filters.after_seq != null) q.set("after_seq", String(filters.after_seq));
    if (filters.limit != null) q.set("limit", String(filters.limit));
    const qs = q.toString();
    return this.request<JournalEntryDTO[]>(
      `/api/tasks/${taskId}/journal${qs ? `?${qs}` : ""}`,
    );
  }
  addTaskJournalNote(taskId: string, body: JournalNoteBody) {
    return this.request<JournalEntryDTO>(`/api/tasks/${taskId}/journal`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  listBoardFrictions(boardId: string, limit?: number) {
    const qs = limit != null ? `?limit=${limit}` : "";
    return this.request<BoardFrictionDTO[]>(
      `/api/boards/${boardId}/frictions${qs}`,
    );
  }
  editTaskPlanningArtifact(
    taskId: string,
    name: string,
    content: string,
    etag?: string | null,
  ) {
    return this.request<{ ok: boolean; path: string; etag: string }>(
      `/api/tasks/${taskId}/planning/artifacts/${encodeURIComponent(name)}`,
      {
        method: "PUT",
        body: JSON.stringify({ content }),
        headers: etag ? { "If-Match": etag } : undefined,
      },
    );
  }
  setTyping(taskId: string, agentId: string, state: "start" | "stop") {
    return this.request<{ ok: boolean }>(
      `/api/tasks/${taskId}/agents/${agentId}/typing`,
      { method: "POST", body: JSON.stringify({ state }) },
    );
  }
  uploadTaskAttachments(taskId: string, agentId: string, files: File[]) {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    const q = new URLSearchParams({ agent_id: agentId });
    return this.request<AttachmentDTO[]>(
      `/api/tasks/${taskId}/attachments?${q}`,
      { method: "POST", body: fd },
    );
  }
  listTaskRuns(taskId: string, agentId?: string) {
    const q = new URLSearchParams();
    if (agentId) q.set("agent_id", agentId);
    const qs = q.toString();
    return this.request<TaskRunDTO[]>(
      `/api/tasks/${taskId}/runs${qs ? `?${qs}` : ""}`,
    );
  }
  listTaskAgentMessages(taskId: string, agentId: string) {
    return this.request<MessageDTO[]>(
      `/api/tasks/${taskId}/agents/${agentId}/messages?limit=500`,
    );
  }
  listTaskAgentAttempts(taskId: string, agentId: string) {
    return this.request<AttemptDTO[]>(
      `/api/tasks/${taskId}/agents/${agentId}/conversations`,
    );
  }
  listTaskAttemptMessages(taskId: string, agentId: string, convId: string) {
    return this.request<MessageDTO[]>(
      `/api/tasks/${taskId}/agents/${agentId}/conversations/${convId}/messages?limit=500`,
    );
  }
  resetTaskAgentThread(taskId: string, agentId: string) {
    return this.request<AttemptDTO>(
      `/api/tasks/${taskId}/agents/${agentId}/reset`,
      { method: "POST" },
    );
  }
  listTaskComments(taskId: string) {
    return this.request<TaskCommentDTO[]>(`/api/tasks/${taskId}/comments`);
  }
  createTaskComment(
    taskId: string,
    body: string,
    attachments: CommentAttachment[] = [],
    visibleToAgents = true,
  ) {
    return this.request<TaskCommentDTO>(`/api/tasks/${taskId}/comments`, {
      method: "POST",
      body: JSON.stringify({
        body,
        attachments,
        visible_to_agents: visibleToAgents,
      }),
    });
  }
  updateTaskComment(
    taskId: string,
    commentId: string,
    patch: { body?: string; visible_to_agents?: boolean },
  ) {
    return this.request<TaskCommentDTO>(
      `/api/tasks/${taskId}/comments/${commentId}`,
      { method: "PATCH", body: JSON.stringify(patch) },
    );
  }
  uploadCommentAttachments(taskId: string, files: File[]) {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    return this.request<CommentAttachment[]>(
      `/api/tasks/${taskId}/comment-attachments`,
      { method: "POST", body: fd },
    );
  }
  /**
   * Fetch a task-workspace file as a Blob using the bearer token. Needed for
   * previews/downloads because `<img>`/`<a>` can't attach the auth header — the
   * caller turns the Blob into an object URL.
   */
  async taskFileBlob(taskId: string, path: string): Promise<Blob> {
    const token = await this.getToken();
    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const q = new URLSearchParams({ path });
    const res = await fetch(apiUrl(`/api/tasks/${taskId}/files/raw?${q}`), {
      headers,
    });
    if (!res.ok) throw new ApiError(res.status, await extractError(res));
    return res.blob();
  }
  /**
   * Authenticated GET of an arbitrary same-origin `/api` URL as a Blob. Used for
   * inline images whose URL the backend pre-builds: `<img>` can't attach the
   * bearer token (so it falls back to cookie auth and may 401), so we fetch the
   * bytes here and the caller renders an object URL instead.
   */
  async fetchBlob(url: string): Promise<Blob> {
    const token = await this.getToken();
    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const res = await fetch(apiUrl(url), { headers });
    if (!res.ok) throw new ApiError(res.status, await extractError(res));
    return res.blob();
  }
  deleteTaskComment(taskId: string, commentId: string) {
    return this.request<{ ok: boolean }>(
      `/api/tasks/${taskId}/comments/${commentId}`,
      { method: "DELETE" },
    );
  }
  listTaskActivity(taskId: string) {
    return this.request<TaskActivityDTO[]>(`/api/tasks/${taskId}/activity`);
  }
  listUsers(q?: string) {
    const qs = q ? `?q=${encodeURIComponent(q)}` : "";
    return this.request<UserDTO[]>(`/api/users${qs}`);
  }
  getRun(runId: string) {
    return this.request<TaskRunDTO>(`/api/runs/${runId}`);
  }
  runStats(boardId: string, days = 30, agentId?: string) {
    const q = new URLSearchParams({ board_id: boardId, days: String(days) });
    if (agentId) q.set("agent_id", agentId);
    return this.request<RunStatsResponse>(`/api/runs/stats?${q.toString()}`);
  }
  cancelTaskRun(runId: string) {
    return this.request<TaskRunDTO>(`/api/runs/${runId}/cancel`, {
      method: "POST",
    });
  }

  // ── shared task workspace files ──────────────────────────────────
  getTaskWorkspaceTree(taskId: string, path = "", depth = 1) {
    const q = new URLSearchParams({ path, depth: String(depth) });
    return this.request<WorkspaceTreeResponse>(
      `/api/tasks/${taskId}/files/tree?${q}`,
    );
  }
  getTaskWorkspaceFile(taskId: string, path: string) {
    const q = new URLSearchParams({ path });
    return this.request<WorkspaceFileResponse>(
      `/api/tasks/${taskId}/files?${q}`,
    );
  }
  writeTaskWorkspaceFile(taskId: string, path: string, content: string) {
    return this.request<WorkspaceFileResponse>(`/api/tasks/${taskId}/files`, {
      method: "PUT",
      body: JSON.stringify({ path, content }),
    });
  }
  deleteTaskWorkspaceFile(taskId: string, path: string) {
    const q = new URLSearchParams({ path });
    return this.request<{ ok: boolean }>(`/api/tasks/${taskId}/files?${q}`, {
      method: "DELETE",
    });
  }
  deleteCommentAttachment(taskId: string, path: string) {
    const q = new URLSearchParams({ path });
    return this.request<{ ok: boolean }>(
      `/api/tasks/${taskId}/comment-attachments?${q}`,
      { method: "DELETE" },
    );
  }
  deleteConversationAttachment(convId: string, attId: string) {
    return this.request<{ ok: boolean }>(
      `/api/conversations/${convId}/attachments/${attId}`,
      { method: "DELETE" },
    );
  }
  deleteTaskAttachment(taskId: string, agentId: string, attId: string) {
    const q = new URLSearchParams({ agent_id: agentId });
    return this.request<{ ok: boolean }>(
      `/api/tasks/${taskId}/attachments/${attId}?${q}`,
      { method: "DELETE" },
    );
  }
  taskWorkspaceFileRawUrl(taskId: string, path: string) {
    const q = new URLSearchParams({ path });
    return apiUrl(`/api/tasks/${taskId}/files/raw?${q}`);
  }

  // ── task code changes (git diff of per-task repo copies) ─────────
  getTaskChanges(taskId: string) {
    return this.request<TaskChangesResponse>(`/api/tasks/${taskId}/changes`);
  }
  getTaskChangeDiff(taskId: string, repo: string, path: string) {
    const q = new URLSearchParams({ repo, path });
    return this.request<TaskFileDiff>(
      `/api/tasks/${taskId}/changes/diff?${q}`,
    );
  }

  // ── code repositories ────────────────────────────────────────────
  listRepos() {
    return this.request<RepoDTO[]>("/api/repos");
  }
  createRepo(body: RepoCreateBody) {
    return this.request<RepoDTO>("/api/repos", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  patchRepo(repoId: string, body: RepoUpdateBody) {
    return this.request<RepoDTO>(`/api/repos/${repoId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }
  deleteRepo(repoId: string) {
    return this.request<{ ok: boolean }>(`/api/repos/${repoId}`, {
      method: "DELETE",
    });
  }
  cloneRepo(repoId: string) {
    return this.request<RepoSyncResult>(`/api/repos/${repoId}/clone`, {
      method: "POST",
    });
  }
  pullRepo(repoId: string) {
    return this.request<RepoSyncResult>(`/api/repos/${repoId}/pull`, {
      method: "POST",
    });
  }
  repoStatus(repoId: string) {
    return this.request<RepoStatusDTO>(`/api/repos/${repoId}/status`);
  }
  listBoardRepos(boardId: string) {
    return this.request<BoardReposResponse>(`/api/boards/${boardId}/repos`);
  }
  assignBoardRepo(
    boardId: string,
    repoId: string,
    branchOverride?: string | null,
    allowPush?: boolean,
    isWiki?: boolean,
  ) {
    return this.request<BoardRepoDTO>(`/api/boards/${boardId}/repos`, {
      method: "POST",
      body: JSON.stringify({
        repo_id: repoId,
        branch_override: branchOverride ?? null,
        ...(allowPush === undefined ? {} : { allow_push: allowPush }),
        ...(isWiki === undefined ? {} : { is_wiki: isWiki }),
      }),
    });
  }
  unassignBoardRepo(boardId: string, repoId: string) {
    return this.request<{ ok: boolean }>(
      `/api/boards/${boardId}/repos/${repoId}`,
      { method: "DELETE" },
    );
  }
  listTaskRepos(taskId: string) {
    return this.request<TaskRepoDir[]>(`/api/tasks/${taskId}/repos`);
  }
  resetTaskRepos(taskId: string) {
    return this.request<{ prepared: TaskRepoDir[] }>(
      `/api/tasks/${taskId}/repos/reset`,
      { method: "POST" },
    );
  }
  prepareTaskRepos(taskId: string) {
    return this.request<{ prepared: TaskRepoDir[] }>(
      `/api/tasks/${taskId}/repos/prepare`,
      { method: "POST" },
    );
  }

  // ── communication gateway ────────────────────────────────────────
  commEventTypes() {
    return this.request<{ event_types: string[] }>("/api/comm/event-types");
  }
  commProviders() {
    return this.request<CommProviderDescriptor[]>("/api/comm/providers");
  }
  listCommConnections() {
    return this.request<CommConnectionDTO[]>("/api/comm/connections");
  }
  createCommConnection(body: CommConnectionCreateBody) {
    return this.request<CommConnectionDTO>("/api/comm/connections", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  patchCommConnection(connectionId: string, body: CommConnectionUpdateBody) {
    return this.request<CommConnectionDTO>(`/api/comm/connections/${connectionId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }
  deleteCommConnection(connectionId: string) {
    return this.request<{ ok: boolean }>(`/api/comm/connections/${connectionId}`, {
      method: "DELETE",
    });
  }
  listCommUserLinks(connectionId: string) {
    return this.request<CommUserLinkDTO[]>(
      `/api/comm/connections/${connectionId}/user-links`,
    );
  }
  upsertCommUserLink(
    connectionId: string,
    body: { user_id: string; mm_username?: string | null; mm_user_id?: string | null },
  ) {
    return this.request<{ ok: boolean; mm_username: string | null }>(
      `/api/comm/connections/${connectionId}/user-links`,
      { method: "PUT", body: JSON.stringify(body) },
    );
  }
  autoMatchCommUserLinks(connectionId: string) {
    return this.request<{ ok: boolean; matched: number }>(
      `/api/comm/connections/${connectionId}/user-links/auto-match`,
      { method: "POST" },
    );
  }
  getBoardChannel(boardId: string) {
    return this.request<BoardChannelResponse>(`/api/boards/${boardId}/channel`);
  }
  putBoardChannel(boardId: string, body: BoardChannelUpsertBody) {
    return this.request<BoardChannelDTO>(`/api/boards/${boardId}/channel`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }
  deleteBoardChannel(boardId: string) {
    return this.request<{ ok: boolean }>(`/api/boards/${boardId}/channel`, {
      method: "DELETE",
    });
  }
  testBoardChannel(boardId: string) {
    return this.request<CommTestSendResult>(`/api/boards/${boardId}/channel/test`, {
      method: "POST",
    });
  }
  listBoardDeliveries(boardId: string) {
    return this.request<CommDeliveryDTO[]>(`/api/boards/${boardId}/channel/deliveries`);
  }
}

async function extractError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
    if (typeof data?.error?.message === "string") return data.error.message;
    if (Array.isArray(data?.detail)) {
      return data.detail.map((d: { msg?: string }) => d.msg).join("; ");
    }
    return JSON.stringify(data);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}
