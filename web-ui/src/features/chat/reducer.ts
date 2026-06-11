import type { AgentEvent } from "@/lib/events";
import type { MessageDTO } from "@/api/types";
import type {
  Block,
  ContextSnapshot,
  UsageSnapshot,
  UserAttachment,
} from "./types";

export interface RunState {
  blocks: Block[];
  running: boolean;
  usage: UsageSnapshot | null;
  context: ContextSnapshot | null;
  fatalError: string | null;
}

export type Action =
  | { type: "reset"; blocks: Block[] }
  | { type: "user"; text: string; attachments?: UserAttachment[] }
  | { type: "start" }
  | { type: "event"; event: AgentEvent }
  | { type: "stopped" }
  | { type: "fatal"; message: string };

let counter = 0;
const nid = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${counter++}`;

/** Pull the sender (who authored a turn) off a persisted message, if known. */
function senderFrom(m: MessageDTO): import("./types").Sender | undefined {
  if (!m.sender_type && !m.sender_name && !m.sender_id) return undefined;
  return {
    type: m.sender_type === "agent" ? "agent" : "user",
    id: m.sender_id ?? undefined,
    name: m.sender_name ?? undefined,
    avatar: m.sender_avatar ?? undefined,
  };
}

export const initialRunState: RunState = {
  blocks: [],
  running: false,
  usage: null,
  context: null,
  fatalError: null,
};

/**
 * Convert persisted history into render blocks.
 *
 * The backend returns full content blocks (text / thinking / tool_use /
 * tool_result), so we rebuild the same timeline the live SSE stream
 * produces — including tool cards — rather than collapsing to text only.
 */
export function blocksFromHistory(
  messages: MessageDTO[],
  liveRunId?: string | null,
): Block[] {
  const out: Block[] = [];
  const toolIdx = new Map<string, number>();

  const pushText = (m: MessageDTO, text: string) => {
    if (m.role === "user") {
      out.push({ kind: "user", id: nid("u"), text, sender: senderFrom(m) });
    } else {
      out.push({
        kind: "assistant",
        id: nid("a"),
        runId: m.run_id ?? "history",
        text,
        open: false,
      });
    }
  };

  for (const m of messages) {
    // The active run is reconstructed live from the replayed SSE buffer,
    // so skip its persisted assistant / tool turns here to avoid showing
    // them twice. The user's prompt has no stream event, so keep it.
    if (liveRunId && m.run_id === liveRunId && m.role !== "user") continue;
    const content = Array.isArray(m.content) ? m.content : [];

    // User messages collapse to a single bubble carrying text + any inlined
    // image/file attachments (persisted as image blocks / 📎 pointers).
    if (m.role === "user") {
      const user = userBlockFromContent(m, content);
      if (user) out.push(user);
      continue;
    }

    if (content.length === 0) {
      const text = m.text?.trim();
      if (text) pushText(m, text);
      continue;
    }
    for (const block of content) {
      const type = block.type as string;
      if (type === "text") {
        const text = typeof block.text === "string" ? block.text : "";
        if (text.trim()) pushText(m, text);
      } else if (type === "thinking") {
        const t = typeof block.thinking === "string" ? block.thinking : "";
        if (t.trim()) {
          out.push({
            kind: "thinking",
            id: nid("t"),
            runId: m.run_id ?? "history",
            text: t,
          });
        }
      } else if (type === "tool_use") {
        const toolId = typeof block.id === "string" ? block.id : nid("tid");
        out.push({
          kind: "tool",
          id: nid("tool"),
          toolId,
          name: typeof block.name === "string" ? block.name : "tool",
          input: (block.input as Record<string, unknown>) ?? {},
          status: "success",
          progress: "",
        });
        toolIdx.set(toolId, out.length - 1);
      } else if (type === "tool_result") {
        const refId =
          typeof block.tool_use_id === "string" ? block.tool_use_id : "";
        const idx = toolIdx.get(refId);
        if (idx != null) {
          const b = out[idx];
          if (b.kind === "tool") {
            out[idx] = {
              ...b,
              status: block.is_error ? "error" : "success",
              outputPreview:
                toolResultPreview(block.content) || b.outputPreview,
            };
          }
        }
      }
      // image / file blocks only appear on user messages (handled above).
    }
  }
  return out;
}

/** Matches the non-image attachment pointer emitted by the backend. */
const FILE_POINTER = /^📎 attached file `([^`]+)`/u;

/** Build a single user bubble from a persisted user message's content. */
function userBlockFromContent(
  m: MessageDTO,
  content: Array<Record<string, unknown>>,
): Block | null {
  const texts: string[] = [];
  const attachments: UserAttachment[] = [];

  if (content.length === 0) {
    const t = m.text?.trim();
    if (!t) return null;
    return { kind: "user", id: nid("u"), text: t, sender: senderFrom(m) };
  }

  for (const block of content) {
    const type = block.type as string;
    if (type === "text") {
      const text = typeof block.text === "string" ? block.text : "";
      const ptr = FILE_POINTER.exec(text.trim());
      if (ptr) {
        attachments.push({ kind: "file", id: nid("att"), name: ptr[1] });
      } else if (text.trim()) {
        texts.push(text);
      }
    } else if (type === "image") {
      const mime =
        typeof block.media_type === "string" ? block.media_type : "image/png";
      const data = typeof block.data === "string" ? block.data : "";
      const src =
        block.source_type === "url" ? data : `data:${mime};base64,${data}`;
      attachments.push({
        kind: "image",
        id: nid("att"),
        name: "image",
        mime,
        url: src,
      });
    }
  }

  if (!texts.length && !attachments.length) return null;
  return {
    kind: "user",
    id: nid("u"),
    text: texts.join("\n"),
    attachments: attachments.length ? attachments : undefined,
    sender: senderFrom(m),
  };
}

/** Best-effort string preview of a persisted tool_result payload. */
function toolResultPreview(content: unknown): string {
  if (typeof content === "string") return content.slice(0, 4000);
  if (Array.isArray(content)) {
    const parts = content.map((b) => {
      if (b && typeof b === "object") {
        const bb = b as Record<string, unknown>;
        return typeof bb.text === "string" ? bb.text : JSON.stringify(bb);
      }
      return String(b);
    });
    return parts.join("\n").slice(0, 4000);
  }
  return "";
}

function lastBlock(blocks: Block[]): Block | undefined {
  return blocks[blocks.length - 1];
}

export function runReducer(state: RunState, action: Action): RunState {
  switch (action.type) {
    case "reset":
      return { ...initialRunState, blocks: action.blocks };
    case "user":
      return {
        ...state,
        blocks: [
          ...state.blocks,
          {
            kind: "user",
            id: nid("u"),
            text: action.text,
            attachments: action.attachments,
          },
        ],
      };
    case "start":
      return { ...state, running: true, fatalError: null };
    case "stopped":
      return { ...state, running: false, blocks: closeOpen(state.blocks) };
    case "fatal":
      return { ...state, running: false, fatalError: action.message };
    case "event":
      return applyEvent(state, action.event);
    default:
      return state;
  }
}

function closeOpen(blocks: Block[]): Block[] {
  return blocks.map((b) =>
    b.kind === "assistant" && b.open ? { ...b, open: false } : b,
  );
}

function applyEvent(state: RunState, ev: AgentEvent): RunState {
  const blocks = [...state.blocks];

  switch (ev.type) {
    case "text_delta": {
      const last = lastBlock(blocks);
      if (last && last.kind === "assistant" && last.open) {
        blocks[blocks.length - 1] = { ...last, text: last.text + ev.text };
      } else {
        blocks.push({
          kind: "assistant",
          id: nid("a"),
          runId: ev.run_id,
          text: ev.text,
          open: true,
        });
      }
      return { ...state, blocks };
    }

    case "thinking": {
      const last = lastBlock(blocks);
      if (last && last.kind === "thinking") {
        blocks[blocks.length - 1] = { ...last, text: last.text + ev.thinking };
      } else {
        blocks.push({
          kind: "thinking",
          id: nid("t"),
          runId: ev.run_id,
          text: ev.thinking,
        });
      }
      return { ...state, blocks };
    }

    case "tool_use_start": {
      // A tool starting closes any open assistant bubble (new turn after).
      const closed = closeOpen(blocks);
      closed.push({
        kind: "tool",
        id: nid("tool"),
        toolId: ev.tool_id,
        name: ev.tool_name,
        input: ev.input ?? {},
        status: "running",
        progress: "",
      });
      return { ...state, blocks: closed };
    }

    case "tool_use_progress": {
      const idx = findTool(blocks, ev.tool_id);
      if (idx >= 0 && ev.chunk) {
        const b = blocks[idx];
        if (b.kind === "tool") {
          blocks[idx] = { ...b, progress: (b.progress + ev.chunk).slice(-4000) };
        }
      }
      return { ...state, blocks };
    }

    case "tool_use_end": {
      const idx = findTool(blocks, ev.tool_id);
      if (idx >= 0) {
        const b = blocks[idx];
        if (b.kind === "tool") {
          blocks[idx] = {
            ...b,
            status: ev.is_error || !ev.success ? "error" : "success",
            outputPreview: ev.output_preview ?? undefined,
            durationMs: ev.duration_ms,
          };
        }
      }
      return { ...state, blocks };
    }

    case "subagent_spawned": {
      blocks.push({
        kind: "subagent",
        id: nid("sub"),
        childRunId: ev.child_run_id,
        agentType: ev.agent_type || ev.profile_name,
        description: ev.description,
        status: "running",
      });
      return { ...state, blocks };
    }

    case "subagent_progress": {
      const idx = findSub(blocks, ev.child_run_id);
      if (idx >= 0) {
        const b = blocks[idx];
        if (b.kind === "subagent") {
          blocks[idx] = { ...b, lastTool: ev.last_tool_name ?? b.lastTool };
        }
      }
      return { ...state, blocks };
    }

    case "subagent_completed": {
      const idx = findSub(blocks, ev.child_run_id);
      if (idx >= 0) {
        const b = blocks[idx];
        if (b.kind === "subagent") {
          blocks[idx] = {
            ...b,
            status:
              ev.status === "completed"
                ? "completed"
                : ev.status === "killed"
                  ? "killed"
                  : "failed",
            tokens: ev.total_tokens,
            error: ev.error ?? undefined,
          };
        }
      }
      return { ...state, blocks };
    }

    case "web_attachment": {
      blocks.push({
        kind: "attachment",
        id: nid("att"),
        filename: ev.attachment.filename,
        caption: ev.attachment.caption,
        url: ev.attachment.url,
        size: ev.attachment.size,
      });
      return { ...state, blocks };
    }

    case "web_message": {
      blocks.push({
        kind: "assistant",
        id: nid("a"),
        runId: "web",
        text: ev.text,
        open: false,
      });
      return { ...state, blocks };
    }

    case "compaction": {
      blocks.push({
        kind: "notice",
        id: nid("n"),
        variant: "compaction",
        text: `History compacted (${ev.before_tokens} → ${ev.after_tokens} tokens)`,
      });
      return { ...state, blocks };
    }

    case "steer_applied": {
      blocks.push({
        kind: "notice",
        id: nid("n"),
        variant: "steer",
        text: `Steered with ${ev.count} message${ev.count === 1 ? "" : "s"}`,
      });
      return { ...state, blocks };
    }

    case "inbox_interrupt": {
      blocks.push({
        kind: "notice",
        id: nid("n"),
        variant: "info",
        text: "Run interrupted by a new message",
      });
      return { ...state, blocks };
    }

    case "request_start": {
      // Each model request reports the live context size + the threshold
      // at which summarisation fires, so the header can show a fill meter.
      const tokens = ev.input_token_estimate ?? state.context?.tokens ?? 0;
      return {
        ...state,
        context: {
          tokens,
          limit: ev.context_limit ?? state.context?.limit ?? null,
          window: ev.context_window ?? state.context?.window ?? null,
        },
      };
    }

    case "budget": {
      return {
        ...state,
        usage: {
          spentUsd: ev.spent_usd,
          spentTokens: ev.spent_tokens,
          inputTokens: ev.spent_input_tokens ?? 0,
          outputTokens: ev.spent_output_tokens ?? 0,
          cacheReadTokens: ev.spent_cache_read_tokens ?? 0,
          cacheCreationTokens: ev.spent_cache_creation_tokens ?? 0,
        },
      };
    }

    case "error": {
      blocks.push({
        kind: "notice",
        id: nid("n"),
        variant: "error",
        text: `${ev.error_class}: ${ev.message}`,
      });
      return { ...state, blocks: closeOpen(blocks) };
    }

    case "run_end": {
      return { ...state, running: false, blocks: closeOpen(blocks) };
    }

    default:
      return state;
  }
}

function findTool(blocks: Block[], toolId: string): number {
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i];
    if (b.kind === "tool" && b.toolId === toolId) return i;
  }
  return -1;
}

function findSub(blocks: Block[], childRunId: string): number {
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i];
    if (b.kind === "subagent" && b.childRunId === childRunId) return i;
  }
  return -1;
}
