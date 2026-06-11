// REST DTOs mirrored from deep_agent/server/schemas.py + record.py.

export interface Me {
  user_id: string;
  email?: string | null;
  is_admin: boolean;
}

export interface ProfileDTO {
  name: string;
  status: string;
  has_mattermost: boolean;
  has_cron: boolean;
  model?: string | null;
}

export type ConversationStatus = "active" | "archived" | "deleted";

export interface ConversationSummary {
  conv_id: string;
  profile_name: string;
  title: string;
  status: ConversationStatus;
  updated_at_ms: number;
  last_run_at_ms: number | null;
  total_runs: number;
}

export interface ConversationRecord {
  conv_id: string;
  profile_name: string;
  title: string;
  status: ConversationStatus;
  created_at_ms: number;
  updated_at_ms: number;
  last_run_at_ms: number | null;
  workspace_root: string | null;
  total_cost_usd: number;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read_tokens: number;
  total_cache_creation_tokens: number;
  total_runs: number;
  metadata: Record<string, unknown>;
}

export interface RunRecord {
  run_id: string;
  conv_id: string;
  status: "pending" | "running" | "done" | "error" | "cancelled";
  started_at_ms: number;
  ended_at_ms: number | null;
  prompt: string;
  final_answer: string | null;
  cost_usd: number;
  tokens: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  error: string | null;
}

export interface ConversationDetail {
  conversation: ConversationRecord;
  runs: RunRecord[];
  /** Run id currently streaming server-side, or null when idle. */
  active_run_id: string | null;
  /** Persisted context fill (tokens vs. summary threshold), or null. */
  context: {
    tokens: number;
    limit: number | null;
    window: number | null;
  } | null;
}

export interface MessageDTO {
  seq: number;
  role: string;
  content: Array<Record<string, unknown>>;
  text: string;
  created_at_ms: number;
  run_id: string | null;
  /** Who authored this turn: "user" (a human collaborator) or "agent". */
  sender_type?: string | null;
  /** Stable id of the sender (board user id for humans, agent id for agents). */
  sender_id?: string | null;
  sender_name?: string | null;
  sender_avatar?: string | null;
}

export interface TodoDTO {
  id: string;
  content: string;
  status: string;
}
export interface PlanDTO {
  todos: TodoDTO[];
  summary: {
    total: number;
    pending: number;
    in_progress: number;
    completed: number;
    cancelled: number;
  };
}

export interface SearchHit {
  conv_id: string;
  message_seq: number;
  role: string;
  text_snippet: string;
  score: number;
}

export interface DeliverTarget {
  platform: "mattermost";
  channel_id: string;
}

export interface CronJob {
  id: string;
  name: string;
  schedule: string;
  prompt: string;
  deliver: DeliverTarget;
  enabled: boolean;
  run_once: boolean;
  created_at_ms: number;
  next_run_at_ms: number;
  last_run_at_ms: number | null;
  last_status: "ok" | "failed" | null;
  last_error: string | null;
  fire_count: number;
  created_by: string | null;
}

export interface CreateCronBody {
  profile: string;
  name: string;
  prompt: string;
  deliver: DeliverTarget;
  schedule?: string | null;
  run_at?: string | null;
}

export interface PatchCronBody {
  name?: string;
  prompt?: string;
  schedule?: string;
  enabled?: boolean;
  deliver?: DeliverTarget;
}

// Workspace file viewer (design 26).
export interface WorkspaceFileNode {
  name: string;
  path: string;
  kind: "file" | "dir";
  size?: number | null;
  children?: WorkspaceFileNode[] | null;
}

export interface WorkspaceTreeResponse {
  root: string;
  entries: WorkspaceFileNode[];
  truncated: boolean;
}

export interface WorkspaceFileResponse {
  path: string;
  content: string;
  size: number;
  encoding: string;
  truncated: boolean;
}

// ── Agent-team platform (plan 16): boards + tasks ───────────────────

export interface BoardColumn {
  key: string;
  name: string;
}

export interface BoardDTO {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  owner_id: string;
  columns: BoardColumn[];
  /** Agents staffing this board — tasks only show these agents. */
  agent_ids?: string[];
  archived: boolean;
  created_at: string;
  updated_at: string;
  /** The requesting user's role on this board (owner/editor/viewer). */
  my_role: BoardRole | null;
  /** Jira sync config (the API token is never returned, only its presence). */
  jira_enabled?: boolean;
  jira_base_url?: string | null;
  jira_email?: string | null;
  jira_project_key?: string | null;
  jira_mappings?: Record<string, Record<string, string>>;
  jira_sync_filter?: JiraSyncFilter;
  jira_has_token?: boolean;
}

/**
 * Jira-side filter that narrows which project issues a board import pulls in
 * (project-agnostic, AND-ed). Translated to JQL on the server.
 */
export interface JiraSyncFilter {
  /** Jira issue type names, e.g. ["Story", "Bug"]. */
  issue_types?: string[];
  /** Jira status categories: "To Do" | "In Progress" | "Done". */
  status_categories?: string[];
  /** Only issues updated within the last N days (omit/0 = no limit). */
  updated_within_days?: number | null;
}

/** Summary returned by a batch board sync. */
export interface JiraBatchResult {
  synced: number;
  skipped: number;
  failed: number;
  errors: string[];
}

/** A project issue offered for import, with its Jira-side fields. */
export interface JiraPreviewItem {
  jira_key: string;
  title: string;
  /** Raw Jira names (fallback when no local mapping exists). */
  jira_type?: string | null;
  jira_priority?: string | null;
  /** Mapped to local values so the UI can reuse its own glyphs. */
  task_type?: TaskType | null;
  priority?: TaskPriority | null;
  /** Display label for the (mapped) status — board column name or Jira status. */
  status?: string | null;
  /** True when a task on this board is already linked to this key. */
  exists: boolean;
  /** The linked task's human key (e.g. `T-12`) when `exists` is true. */
  human_key?: string | null;
}

export interface JiraPreviewResponse {
  items: JiraPreviewItem[];
}

export type BoardRole = "owner" | "editor" | "viewer";

export interface BoardMemberDTO {
  board_id: string;
  user_id: string;
  role: BoardRole;
  email?: string | null;
  display_name?: string | null;
  avatar_url?: string | null;
}

export type TaskPriority =
  | "highest"
  | "high"
  | "medium"
  | "low"
  | "lowest";

/** Jira-style issue type, persisted on the task (defaults to "task"). */
export type TaskType =
  | "task"
  | "story"
  | "bug"
  | "epic"
  | "subtask"
  | "agent";

export interface TaskDTO {
  id: string;
  human_key: string;
  board_id: string;
  title: string;
  description?: string | null;
  task_type: TaskType;
  status: string;
  position: number;
  assignee_id?: string | null;
  labels: string[];
  priority?: TaskPriority | null;
  jira_key?: string | null;
  jira_url?: string | null;
  workspace_path: string;
  created_by: string;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateBoardBody {
  name: string;
  slug?: string | null;
  description?: string | null;
  columns?: BoardColumn[] | null;
}

export interface PatchBoardBody {
  name?: string;
  description?: string | null;
  columns?: BoardColumn[];
  /** Agents staffing this board — tasks only show these agents. */
  agent_ids?: string[];
  archived?: boolean;
  /** Jira sync config. Omit jira_api_token to keep it; send "" to clear it. */
  jira_enabled?: boolean;
  jira_base_url?: string | null;
  jira_email?: string | null;
  jira_api_token?: string | null;
  jira_project_key?: string | null;
  jira_mappings?: Record<string, Record<string, string>>;
  jira_sync_filter?: JiraSyncFilter;
}

export interface CreateTaskBody {
  board_id: string;
  title: string;
  status?: string | null;
  description?: string | null;
  task_type?: TaskType | null;
  assignee_id?: string | null;
  labels?: string[] | null;
  priority?: TaskPriority | null;
  jira_key?: string | null;
  jira_url?: string | null;
}

export interface PatchTaskBody {
  title?: string;
  description?: string | null;
  task_type?: TaskType;
  status?: string;
  assignee_id?: string | null;
  labels?: string[];
  priority?: TaskPriority | null;
  jira_key?: string | null;
  jira_url?: string | null;
}

export interface MoveTaskBody {
  status: string;
  position: number;
}

export interface AddMemberBody {
  user_id?: string | null;
  email?: string | null;
  role?: BoardRole;
}

// ── Agent-team platform (plan 16) Phase 2: agents, mentions, runs ───

export interface AgentDTO {
  id: string;
  display_name: string;
  description?: string | null;
  avatar_url?: string | null;
  model?: string | null;
  mentionable: boolean;
  enabled: boolean;
  /** Live supervisor status ("running"/"error"/...). */
  status?: string | null;
}

export type TaskRunStatus =
  | "queued"
  | "running"
  | "done"
  | "error"
  | "cancelled";

export interface TaskRunDTO {
  id: string;
  human_key: string;
  task_id: string;
  agent_id: string;
  conversation_id?: string | null;
  trigger: string;
  actor_id?: string | null;
  status: TaskRunStatus;
  prompt?: string | null;
  final_answer?: string | null;
  error?: string | null;
  tokens: number;
  cost_usd: number;
  started_at?: string | null;
  ended_at?: string | null;
  created_at: string;
}

export interface RunStatsBucket {
  date: string;
  runs: number;
  tokens: number;
  cost_usd: number;
}

export interface RunStatsAgent {
  agent_id: string;
  runs: number;
  tokens: number;
  cost_usd: number;
}

export interface RunStatsResponse {
  board_id: string;
  from_date: string;
  to_date: string;
  total_runs: number;
  total_tokens: number;
  total_cost_usd: number;
  success_rate?: number | null;
  avg_duration_ms?: number | null;
  avg_cycle_time_ms?: number | null;
  by_status: Record<string, number>;
  buckets: RunStatsBucket[];
  by_agent: RunStatsAgent[];
}

export interface MentionResponse {
  run: TaskRunDTO;
  conversation_id: string;
  stream_url: string;
}

export interface MentionBody {
  agent_id: string;
  body: string;
  attachment_ids?: string[];
}

/** Metadata returned by the attachment upload endpoints. */
export interface AttachmentDTO {
  id: string;
  kind: "image" | "text" | "binary";
  media_type: string;
  filename: string;
  size_bytes: number;
}

export interface AttemptDTO {
  id: string;
  task_id: string;
  agent_id: string;
  conv_id: string;
  attempt: number;
  is_active: boolean;
  created_at: string;
  title?: string | null;
}

export interface CommentAttachment {
  id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  /** Task-workspace-relative path (under `_notes/`) for the raw file route. */
  path: string;
}

export interface TaskCommentDTO {
  id: string;
  task_id: string;
  author_id: string;
  author_name?: string | null;
  author_avatar?: string | null;
  body: string;
  attachments?: CommentAttachment[];
  visible_to_agents?: boolean;
  created_at: string;
  updated_at: string;
}

export interface UserDTO {
  id: string;
  email?: string | null;
  display_name?: string | null;
  avatar_url?: string | null;
}

export interface TaskActivityDTO {
  id: string;
  task_id: string;
  actor_id?: string | null;
  actor_name?: string | null;
  actor_avatar?: string | null;
  kind: string;
  data: {
    field?: string;
    from?: string | null;
    to?: string | null;
    status?: string | null;
  };
  created_at: string;
}

// ── Code repositories (board repos) ─────────────────────────────────

export type RepoAuthType = "none" | "token" | "ssh";
export type RepoScheduleMode = "off" | "interval" | "cron";
export type RepoCloneStatus = "absent" | "cloning" | "cloned" | "error";

export interface RepoDTO {
  id: string;
  owner_id: string | null;
  name: string;
  slug: string;
  git_url: string;
  default_branch: string | null;
  auth_type: RepoAuthType;
  auth_username: string | null;
  /** True when a credential is stored — the secret itself is never returned. */
  has_secret: boolean;
  schedule_mode: RepoScheduleMode;
  schedule_interval_seconds: number;
  schedule_cron: string | null;
  /** Whether agents may push this repo (the git_push tool). */
  allow_push: boolean;
  committer_name: string | null;
  committer_email: string | null;
  clone_status: RepoCloneStatus;
  last_synced_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
  next_pull_at: string | null;
  used_by_boards: number;
  archived: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface RepoCreateBody {
  name: string;
  git_url: string;
  default_branch?: string | null;
  auth_type?: RepoAuthType;
  auth_username?: string | null;
  auth_secret?: string | null;
  schedule_mode?: RepoScheduleMode;
  schedule_interval_seconds?: number;
  schedule_cron?: string | null;
  allow_push?: boolean;
  committer_name?: string | null;
  committer_email?: string | null;
}

export interface RepoUpdateBody {
  name?: string;
  git_url?: string;
  default_branch?: string | null;
  auth_type?: RepoAuthType;
  auth_username?: string | null;
  /** Omit to keep the stored secret; send "" to clear it. */
  auth_secret?: string | null;
  schedule_mode?: RepoScheduleMode;
  schedule_interval_seconds?: number;
  schedule_cron?: string | null;
  allow_push?: boolean;
  committer_name?: string | null;
  committer_email?: string | null;
  archived?: boolean;
}

export interface BoardRepoDTO {
  repo: RepoDTO;
  branch_override: string | null;
  /** This board's push opt-in (effective push also needs repo.allow_push). */
  allow_push: boolean;
}

export interface BoardReposResponse {
  assigned: BoardRepoDTO[];
  available: RepoDTO[];
}

export interface RepoSyncResult {
  ok: boolean;
  action: string;
  message: string;
  repo: RepoDTO | null;
}

export interface TaskRepoDir {
  slug: string;
  /** Path relative to the task workspace. */
  path: string;
  /** True when the working copy exists in the task folder. */
  present: boolean;
}

export interface RepoStatusDTO {
  repo_id: string;
  is_git: boolean;
  branch?: string | null;
  last_commit?: string | null;
  behind?: number | null;
  ahead?: number | null;
  error?: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
