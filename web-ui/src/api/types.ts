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

/** One MCP server entry (shape mirrors the agent MCP config the backend reads). */
export interface McpServerConfig {
  /** Remote (HTTP/SSE) server URL, e.g. "https://mcp.example.com/sse". */
  url?: string;
  /** Local stdio server command (mutually exclusive with `url`). */
  command?: string;
  args?: string[];
  /** Bearer token or auth string for a remote server. */
  auth?: string;
  /** Extra HTTP headers for a remote server. */
  headers?: Record<string, string>;
  /** Environment variables for a local stdio server. */
  env?: Record<string, string>;
}

/** A CLI agent's MCP config: a named map of servers. */
export interface AgentMcpConfig {
  mcpServers: Record<string, McpServerConfig>;
}

/** Per-CLI-agent MCP config keyed by the `cli:<engine>` alias. */
export type BoardAgentMcp = Record<string, AgentMcpConfig>;

export interface BoardDTO {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  owner_id: string;
  columns: BoardColumn[];
  /** Agents staffing this board — tasks only show these agents. */
  agent_ids?: string[];
  /** Direct-CLI aliases (`cli:<engine>`) enabled on this board. */
  cli_target_ids?: string[];
  /** Skill pack names available to this board's direct-CLI agents. */
  skill_ids?: string[];
  /** Per-CLI-agent MCP config (owner-only; empty for non-owners). */
  agent_mcp?: BoardAgentMcp;
  /** Reusable starter chat message offered as a one-click first message. */
  starter_prompt?: string;
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
  /** Whether a sync overwrites the local task status with the Jira status. */
  jira_sync_status?: boolean;
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
  /** Human reporter mapped from Jira (by account email). */
  reporter_id?: string | null;
  /** Agent/CLI alias this task is assigned to (autopilot ownership). */
  agent_assignee?: string | null;
  labels: string[];
  priority?: TaskPriority | null;
  jira_key?: string | null;
  jira_url?: string | null;
  workspace_path: string;
  created_by: string;
  archived: boolean;
  created_at: string;
  updated_at: string;
  /** Autonomous-loop objective, persisted when a loop is started. */
  objective?: string | null;
  /** "chat" (default) or "autonomous". */
  execution_mode?: string;
  /** Live loop lifecycle state (null on a plain chat task). */
  loop_state?: LoopState | null;
}

/** Persisted lifecycle state of a task's autonomous loop. */
export type LoopState =
  | "planning"
  | "waiting_plan_approval"
  | "plan_approved"
  | "running"
  | "complete"
  | "waiting_for_human"
  | "plan_change_requested"
  | "waiting_answers"
  | "failed"
  | "cancelled";

/** How a task's plan is produced before autonomous execution. */
export type PlanningMode = "legacy_plan" | "strict_plan";

/** One planning artifact's on-disk metadata (+ text for readable files). */
export interface PlanningArtifactDTO {
  path: string;
  exists: boolean;
  etag?: string | null;
  size?: number;
  updated_at?: string | null;
  content?: string | null;
}

/** One structured question an agent raised for the human. */
export interface PlanningQuestion {
  id: string;
  question: string;
  reason?: string;
  blocking?: boolean;
  /** Suggested choices; the UI always adds an "Other" free-text option. */
  options?: string[];
  /** The human's answer once given ("" while unanswered). */
  answer?: string;
}

/** Snapshot of a task's strict planning phase for the cockpit. */
export interface PlanningInfoDTO {
  task_id: string;
  loop_state?: LoopState | null;
  planning_mode: PlanningMode;
  objective?: string | null;
  is_planning: boolean;
  approved: boolean;
  approved_by?: string | null;
  approved_at?: string | null;
  review_verdict?: string | null;
  last_error?: string | null;
  artifacts: PlanningArtifactDTO[];
  /** Blocking questions an agent raised, shown as cards when waiting for answers. */
  questions?: PlanningQuestion[];
}

/** Body to answer an agent's blocking questions and resume the paused phase. */
export interface PlanningAnswerBody {
  /** Map of question id → chosen option or free text (an "Other" answer). */
  answers: Record<string, string>;
  /** Optional overall remark carried into the agent's resume/re-plan prompt. */
  note?: string | null;
}

/** Body to start the strict planning phase. */
export interface PlanningStartBody {
  planner_id: string;
  reviewer_id?: string | null;
  objective?: string | null;
}

/** Body to approve the plan and start strict execution. */
export interface PlanningRunBody {
  agent_id: string;
  evaluator_id: string;
  /** Execute task-by-task from TASKS.json (default true when a valid list exists). */
  task_graph?: boolean;
  max_attempts?: number;
  max_tokens?: number | null;
  max_cost_usd?: number | null;
  max_wall_seconds?: number | null;
}

/** One entry in a task's semantic journal (sổ cái). */
export interface JournalEntryDTO {
  id: string;
  task_id: string;
  seq: number;
  actor_type: "human" | "agent" | "system";
  actor_id?: string | null;
  phase: string;
  type: string;
  title: string;
  body?: string;
  severity: "info" | "warning" | "blocking";
  refs?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  supersedes_id?: string | null;
  created_at?: string | null;
}

/** One friction signal on the board Friction page (a journal entry + its task). */
export interface BoardFrictionDTO {
  id: string;
  task_id: string;
  task_key: string;
  task_title: string;
  title: string;
  body?: string;
  severity: "info" | "warning" | "blocking";
  phase: string;
  actor_type: "human" | "agent" | "system";
  actor_id?: string | null;
  created_at?: string | null;
}

/** Query filters for the journal timeline. */
export interface JournalFilters {
  type?: string;
  phase?: string;
  severity?: string;
  after_seq?: number;
  limit?: number;
}

/** Body for a manual human journal note. */
export interface JournalNoteBody {
  type?: string;
  title: string;
  body?: string;
  severity?: "info" | "warning" | "blocking";
  refs?: Record<string, unknown>;
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
  /** Direct-CLI aliases (`cli:<engine>`) enabled on this board. */
  cli_target_ids?: string[];
  /** Skill pack names available to this board's direct-CLI agents. */
  skill_ids?: string[];
  /** Per-CLI-agent MCP config keyed by `cli:<engine>` alias. */
  agent_mcp?: BoardAgentMcp;
  /** Reusable starter chat message offered as a one-click first message. */
  starter_prompt?: string;
  archived?: boolean;
  /** Jira sync config. Omit jira_api_token to keep it; send "" to clear it. */
  jira_enabled?: boolean;
  jira_base_url?: string | null;
  jira_email?: string | null;
  jira_api_token?: string | null;
  jira_project_key?: string | null;
  jira_mappings?: Record<string, Record<string, string>>;
  jira_sync_filter?: JiraSyncFilter;
  jira_sync_status?: boolean;
}

export interface CreateTaskBody {
  board_id: string;
  title: string;
  status?: string | null;
  description?: string | null;
  task_type?: TaskType | null;
  assignee_id?: string | null;
  agent_assignee?: string | null;
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
  /** Agent/CLI alias to assign; send "" to clear. */
  agent_assignee?: string | null;
  labels?: string[];
  priority?: TaskPriority | null;
  jira_key?: string | null;
  jira_url?: string | null;
}

export interface MoveTaskBody {
  status: string;
  position: number;
}

// ── CSV import / export ─────────────────────────────────────────────

export type CsvImportAction = "create" | "update" | "error";

export interface CsvImportRow {
  line: number;
  action: CsvImportAction;
  title: string;
  human_key: string | null;
  /** Warnings (lenient fallbacks) or the error reason. */
  message: string;
}

export interface CsvImportPreview {
  rows: CsvImportRow[];
  total: number;
  creates: number;
  updates: number;
  errors: number;
}

export interface CsvImportResult {
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
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

/**
 * A direct CLI engine (Claude/Cursor/Codex) chattable without the LLM. Not an
 * agent: addressed by the synthetic ``cli:<engine>`` alias in {@link id}.
 */
export interface CliTargetDTO {
  /** Synthetic agent alias, e.g. `cli:claude`. */
  id: string;
  engine: string;
  label: string;
  /** Whether the engine's launch command looks installed on the host. */
  available: boolean;
}

export interface SkillPackDTO {
  name: string;
  description: string;
  version?: string | null;
  source?: string | null;
}

// ── Autopilot (per-board auto-pickup of assigned tasks) ──────────────

export type AutopilotScheduleMode = "off" | "interval" | "cron";

export interface AutopilotDTO {
  board_id: string;
  enabled: boolean;
  schedule_mode: AutopilotScheduleMode;
  interval_seconds: number;
  cron?: string | null;
  timezone: string;
  /** Board column *keys* (not labels). */
  source_status: string;
  working_status: string;
  done_status: string;
  error_status: string;
  board_concurrency: number;
  default_agent_concurrency: number;
  /** Map `{agent_alias: max_in_flight}` overriding the default. */
  agent_concurrency: Record<string, number>;
  error_cooldown_seconds: number;
  max_attempts: number;
  prompt_template?: string | null;
  routing_rules: RoutingRule[];
  next_run_at?: string | null;
  last_run_at?: string | null;
  updated_at?: string | null;
}

export interface RoutingRule {
  labels: string[];
  priorities: string[];
  agents: string[];
}

export interface AutopilotRecentItem {
  task_id: string;
  human_key: string;
  title: string;
  status: string;
  agent: string;
  run_status: string;
  at?: string | null;
}

export interface AutopilotSummaryDTO {
  enabled: boolean;
  schedule_mode: AutopilotScheduleMode;
  next_run_at?: string | null;
  last_run_at?: string | null;
  in_flight: number;
  board_concurrency: number;
  runs_today: number;
  recent: AutopilotRecentItem[];
}

export interface PatchAutopilotBody {
  enabled?: boolean;
  schedule_mode?: AutopilotScheduleMode;
  interval_seconds?: number;
  cron?: string | null;
  timezone?: string;
  source_status?: string;
  working_status?: string;
  done_status?: string;
  error_status?: string;
  board_concurrency?: number;
  default_agent_concurrency?: number;
  agent_concurrency?: Record<string, number>;
  error_cooldown_seconds?: number;
  max_attempts?: number;
  prompt_template?: string | null;
  routing_rules?: RoutingRule[];
}

export type TaskScheduleConversationMode = "new" | "continue";

export interface TaskScheduleDTO {
  task_id: string;
  enabled: boolean;
  cron?: string | null;
  timezone: string;
  agent_alias?: string | null;
  prompt?: string | null;
  conversation_mode: TaskScheduleConversationMode;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_run_id?: string | null;
  updated_at?: string | null;
}

export interface PatchTaskScheduleBody {
  enabled?: boolean;
  cron?: string | null;
  timezone?: string;
  agent_alias?: string | null;
  prompt?: string | null;
  conversation_mode?: TaskScheduleConversationMode;
}

export interface TaskScheduleHistoryItem {
  run_id: string;
  human_key: string;
  agent_id: string;
  status: TaskRunStatus;
  created_at?: string | null;
  ended_at?: string | null;
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
  /** Total tokens for the turn (cumulative across the session for direct CLI). */
  total_tokens?: number;
  /** Direct-CLI context-window gauge text, e.g. "45,000/200,000 tokens". */
  cli_usage_text?: string | null;
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

// ── Autonomous loop (generator + evaluator) ─────────────────────────

export type LoopVerdict = "pass" | "fail" | "needs_human";

export interface LoopEvaluationDTO {
  id: string;
  attempt_id: string;
  run_id?: string | null;
  verdict: LoopVerdict;
  score: number;
  missing: string;
  /** Free-form evidence the critic recorded (e.g. `{ checks: "..." }`). */
  evidence?: Record<string, unknown>;
  /** Conversation holding the critic run's verification transcript. */
  conversation_id?: string | null;
  created_at?: string | null;
}

export interface LoopAttemptDTO {
  id: string;
  attempt_no: number;
  status: string;
  /** Terminal loop outcome stamped on the attempt that ended the loop. */
  outcome?: string | null;
  created_at?: string | null;
  ended_at?: string | null;
  /** Generator run that did this iteration's work (used to stream it live). */
  run_id?: string | null;
  /** Conversation holding the iteration's transcript. */
  conversation_id?: string | null;
  /** Critic run that graded this iteration + its fresh verification transcript. */
  critic_run_id?: string | null;
  critic_conversation_id?: string | null;
  evaluations: LoopEvaluationDTO[];
}

export interface LoopInfoDTO {
  task_id: string;
  execution_mode: string;
  loop_state?: LoopState | null;
  objective?: string | null;
  /** Whether a loop is actively running in-process right now. */
  is_running: boolean;
  /** Conversation with the generator's continuous transcript across iterations. */
  generator_conversation_id?: string | null;
  /** The planning phase's transcript conversation + run (null if no plan phase). */
  planner_conversation_id?: string | null;
  planner_run_id?: string | null;
  /** The loop run streaming right now (any role) + its conversation. */
  active_run_id?: string | null;
  active_conversation_id?: string | null;
  attempts: LoopAttemptDTO[];
  /** Live task-graph progress from TASKS.json (empty unless executing task-by-task). */
  tasks?: LoopTaskDTO[];
}

export type LoopTaskStatus =
  | "pending"
  | "in_progress"
  | "complete"
  | "blocked"
  | "skipped";

/** One task from TASKS.json for the cockpit's task-graph progress view. */
export interface LoopTaskDTO {
  id: string;
  title: string;
  status: LoopTaskStatus;
  depends_on: string[];
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
    /** Autopilot activity: the agent alias that picked the task. */
    agent_id?: string | null;
    /** Autopilot activity: the terminal run status that triggered the move. */
    run_status?: string | null;
    /** Schedule activity: conversation mode of the fired run ("new"/"continue"). */
    mode?: string | null;
    /** Schedule activity: why a due fire was skipped. */
    reason?: string | null;
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
  /** Whether this repo is the board's wiki (knowledge base). */
  is_wiki: boolean;
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

// ── communication gateway ────────────────────────────────────────────────

export type CommTagMode = "none" | "assignee" | "creator";

export interface CommProviderField {
  key: string;
  label: string;
  type: "text" | "url" | "secret";
  required: boolean;
  placeholder: string;
  help: string;
}

export interface CommProviderDescriptor {
  id: string;
  label: string;
  fields: CommProviderField[];
  channel_id_label: string;
  channel_id_placeholder: string;
  channel_id_help: string;
}

export interface CommConnectionDTO {
  id: string;
  owner_id: string | null;
  provider: string;
  name: string;
  server_url: string;
  /** True when a bot token is stored — the token itself is never returned. */
  has_token: boolean;
  default_team_id: string | null;
  deep_link_base: string | null;
  archived: boolean;
  used_by_boards: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface CommConnectionCreateBody {
  name: string;
  provider?: string;
  server_url?: string | null;
  bot_token?: string | null;
  default_team_id?: string | null;
  deep_link_base?: string | null;
}

export interface CommConnectionUpdateBody {
  name?: string;
  server_url?: string | null;
  /** Omit to keep the stored token; send "" to clear it. */
  bot_token?: string | null;
  default_team_id?: string | null;
  deep_link_base?: string | null;
  archived?: boolean;
}

export interface CommUserLinkDTO {
  user_id: string;
  email: string | null;
  display_name: string | null;
  role: string | null;
  mm_user_id: string | null;
  mm_username: string | null;
  source: string | null;
}

export interface BoardChannelDTO {
  id: string;
  board_id: string;
  connection_id: string;
  connection_name: string | null;
  provider: string;
  channel_id: string;
  channel_name: string;
  use_threads: boolean;
  event_allowlist: string[];
  tag_mode: CommTagMode;
  enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface BoardChannelResponse {
  channel: BoardChannelDTO | null;
  available_connections: CommConnectionDTO[];
}

export interface BoardChannelUpsertBody {
  connection_id: string;
  channel_id: string;
  channel_name?: string | null;
  use_threads?: boolean;
  event_allowlist?: string[];
  tag_mode?: CommTagMode;
  enabled?: boolean;
}

export interface CommDeliveryDTO {
  id: string;
  task_id: string | null;
  board_id: string | null;
  channel_id: string | null;
  event_type: string;
  provider: string;
  provider_message_id: string | null;
  provider_thread_id: string | null;
  status: string;
  error: string | null;
  created_at: string | null;
  sent_at: string | null;
}

export interface CommTestSendResult {
  ok: boolean;
  provider_message_id: string | null;
  error: string | null;
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
