export type ToolStatus = "running" | "success" | "error";

/** An attachment shown inside a user message bubble (image preview or chip). */
export interface UserAttachment {
  id: string;
  kind: "image" | "file";
  name: string;
  mime?: string;
  /** Data/object URL for image previews (live send or persisted base64). */
  url?: string;
  size?: number;
}

/** Who authored a message — used to render an avatar + name label. */
export interface Sender {
  type?: "user" | "agent";
  id?: string;
  name?: string;
  avatar?: string;
}

export interface UserBlock {
  kind: "user";
  id: string;
  text: string;
  attachments?: UserAttachment[];
  sender?: Sender;
  /** When the message was sent (epoch ms); shown at the start of the message. */
  createdAtMs?: number;
}
export interface AssistantBlock {
  kind: "assistant";
  id: string;
  runId: string;
  text: string;
  open: boolean;
  createdAtMs?: number;
}
export interface ThinkingBlock {
  kind: "thinking";
  id: string;
  runId: string;
  text: string;
  createdAtMs?: number;
}
export interface ToolBlock {
  kind: "tool";
  id: string;
  toolId: string;
  /** Run that produced this tool call; needed to lazy-load the full output. */
  runId?: string;
  name: string;
  input: Record<string, unknown>;
  status: ToolStatus;
  progress: string;
  outputPreview?: string;
  /** True when the result is longer than the inline preview (offer "show more"). */
  truncated?: boolean;
  durationMs?: number;
  createdAtMs?: number;
}
/** One row of a live plan/task checklist (from a CLI agent's plan updates). */
export interface PlanEntry {
  title: string;
  status: "todo" | "in_progress" | "done";
}
export interface PlanBlock {
  kind: "plan";
  id: string;
  runId: string;
  entries: PlanEntry[];
  createdAtMs?: number;
}
export interface SubagentBlock {
  kind: "subagent";
  id: string;
  childRunId: string;
  agentType: string;
  description: string;
  status: "running" | "completed" | "failed" | "killed";
  lastTool?: string;
  tokens?: number;
  error?: string;
  createdAtMs?: number;
}
export interface AttachmentBlock {
  kind: "attachment";
  id: string;
  filename: string;
  caption: string;
  url: string;
  size: number;
  createdAtMs?: number;
}
export interface NoticeBlock {
  kind: "notice";
  id: string;
  variant: "compaction" | "steer" | "error" | "info";
  text: string;
  createdAtMs?: number;
}

export type Block =
  | UserBlock
  | AssistantBlock
  | ThinkingBlock
  | ToolBlock
  | PlanBlock
  | SubagentBlock
  | AttachmentBlock
  | NoticeBlock;

export interface UsageSnapshot {
  spentUsd: number;
  spentTokens: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
}

/** Live context-window occupancy, surfaced by `request_start` events. */
export interface ContextSnapshot {
  /** Estimated tokens currently in the live context window. */
  tokens: number;
  /** Token count at which auto-compaction fires (the summary threshold). */
  limit: number | null;
  /** Full model context window. */
  window: number | null;
}
