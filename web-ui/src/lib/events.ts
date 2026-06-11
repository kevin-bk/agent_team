/**
 * TypeScript mirror of `deep_agent/trajectory/events.py` (design 25 §4.1)
 * plus the two synthetic web-channel frames emitted by
 * `deep_agent/server/web_channel.py`.
 *
 * Kept hand-in-sync with the pydantic union. A future codegen step can
 * derive this from the schema, but the surface is small and stable.
 */

""
export interface BaseEvent {
  type: string;
  conv_id: string;
  run_id: string;
  turn_id?: string | null;
  seq: number;
  timestamp_ms: number;
}

export interface RunStartEvent extends BaseEvent {
  type: "run_start";
  prompt?: string | null;
}
export interface RunEndEvent extends BaseEvent {
  type: "run_end";
  status: "done" | "error" | "cancelled";
  final_answer?: string | null;
}
export interface TextDeltaEvent extends BaseEvent {
  type: "text_delta";
  text: string;
}
export interface ThinkingEvent extends BaseEvent {
  type: "thinking";
  thinking: string;
  signature?: string | null;
}
export interface ToolUseStartEvent extends BaseEvent {
  type: "tool_use_start";
  tool_id: string;
  tool_name: string;
  input: Record<string, unknown>;
}
export interface ToolUseProgressEvent extends BaseEvent {
  type: "tool_use_progress";
  tool_id: string;
  chunk?: string | null;
  progress?: Record<string, unknown> | null;
}
export interface ToolUseEndEvent extends BaseEvent {
  type: "tool_use_end";
  tool_id: string;
  tool_name: string;
  success: boolean;
  duration_ms: number;
  output_preview?: string | null;
  is_error: boolean;
}
export interface CompactionEvent extends BaseEvent {
  type: "compaction";
  strategy: string;
  before_tokens: number;
  after_tokens: number;
}
export interface SubagentSpawnedEvent extends BaseEvent {
  type: "subagent_spawned";
  child_run_id: string;
  spawn_mode: string;
  profile_name: string;
  agent_type: string;
  description: string;
  parent_tool_use_id?: string | null;
  isolation: string;
}
export interface SubagentProgressEvent extends BaseEvent {
  type: "subagent_progress";
  child_run_id: string;
  agent_type: string;
  progress: Record<string, unknown>;
  last_tool_name?: string | null;
}
export interface SubagentCompletedEvent extends BaseEvent {
  type: "subagent_completed";
  child_run_id: string;
  status: string;
  final_answer_preview?: string | null;
  agent_type: string;
  total_tokens: number;
  total_tool_use_count: number;
  duration_ms: number;
  error?: string | null;
}
export interface PermissionAskEvent extends BaseEvent {
  type: "permission_ask";
  tool_name: string;
  tool_input: Record<string, unknown>;
  rationale?: string | null;
}
export interface PermissionDecisionEvent extends BaseEvent {
  type: "permission_decision";
  tool_name: string;
  decision: "allow_once" | "allow_always" | "deny";
  rule_persisted: boolean;
}
export interface RequestStartEvent extends BaseEvent {
  type: "request_start";
  model: string;
  api_mode: string;
  input_token_estimate?: number | null;
  context_window?: number | null;
  context_limit?: number | null;
}
export interface RequestEndEvent extends BaseEvent {
  type: "request_end";
  model: string;
  api_mode: string;
  stop_reason: string;
  duration_ms: number;
}
export interface UsageEvent extends BaseEvent {
  type: "usage";
  usage: {
    input_tokens?: number;
    output_tokens?: number;
    cache_read_tokens?: number;
    cache_write_tokens?: number;
    cost_usd?: number;
    [k: string]: unknown;
  };
}
export interface BudgetEvent extends BaseEvent {
  type: "budget";
  spent_usd: number;
  spent_tokens: number;
  max_usd?: number | null;
  max_tokens?: number | null;
  spent_input_tokens?: number;
  spent_output_tokens?: number;
  spent_cache_read_tokens?: number;
  spent_cache_creation_tokens?: number;
}
export interface HeartbeatEvent extends BaseEvent {
  type: "heartbeat";
}
export interface ErrorEvent extends BaseEvent {
  type: "error";
  error_class: string;
  message: string;
  recoverable: boolean;
  stack?: string | null;
  failover_reason?: string | null;
}
export interface FinalAnswerEvent extends BaseEvent {
  type: "final_answer";
  content?: string | null;
}
export interface InboxItemReceivedEvent extends BaseEvent {
  type: "inbox_item_received";
  item_id: string;
  mode: "queue" | "interrupt" | "steer";
  source: string;
}
export interface InboxInterruptEvent extends BaseEvent {
  type: "inbox_interrupt";
  item_id: string;
}
export interface SteerAppliedEvent extends BaseEvent {
  type: "steer_applied";
  item_ids: string[];
  count: number;
}
export interface InboxPendingEvent extends BaseEvent {
  type: "inbox_pending";
  count: number;
}
export interface BackgroundReviewSummaryEvent extends BaseEvent {
  type: "background_review_summary";
  actions: string[];
  duration_ms: number;
  error?: string | null;
}

/** Synthetic frames from the web channel (not part of the agent loop). */
export interface WebMessageFrame {
  type: "web_message";
  post_id: string;
  role: string;
  text: string;
  thread_root_id?: string | null;
}
export interface WebAttachment {
  id: string;
  filename: string;
  caption: string;
  size: number;
  url: string;
}
export interface WebAttachmentFrame {
  type: "web_attachment";
  post_id: string;
  thread_root_id?: string | null;
  attachment: WebAttachment;
}

export type AgentEvent =
  | RunStartEvent
  | RunEndEvent
  | TextDeltaEvent
  | ThinkingEvent
  | ToolUseStartEvent
  | ToolUseProgressEvent
  | ToolUseEndEvent
  | CompactionEvent
  | SubagentSpawnedEvent
  | SubagentProgressEvent
  | SubagentCompletedEvent
  | PermissionAskEvent
  | PermissionDecisionEvent
  | RequestStartEvent
  | RequestEndEvent
  | UsageEvent
  | BudgetEvent
  | HeartbeatEvent
  | ErrorEvent
  | FinalAnswerEvent
  | InboxItemReceivedEvent
  | InboxInterruptEvent
  | SteerAppliedEvent
  | InboxPendingEvent
  | BackgroundReviewSummaryEvent
  | WebMessageFrame
  | WebAttachmentFrame;
