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
}
export interface AssistantBlock {
  kind: "assistant";
  id: string;
  runId: string;
  text: string;
  open: boolean;
}
export interface ThinkingBlock {
  kind: "thinking";
  id: string;
  runId: string;
  text: string;
}
export interface ToolBlock {
  kind: "tool";
  id: string;
  toolId: string;
  name: string;
  input: Record<string, unknown>;
  status: ToolStatus;
  progress: string;
  outputPreview?: string;
  durationMs?: number;
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
}
export interface AttachmentBlock {
  kind: "attachment";
  id: string;
  filename: string;
  caption: string;
  url: string;
  size: number;
}
export interface NoticeBlock {
  kind: "notice";
  id: string;
  variant: "compaction" | "steer" | "error" | "info";
  text: string;
}

export type Block =
  | UserBlock
  | AssistantBlock
  | ThinkingBlock
  | ToolBlock
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
