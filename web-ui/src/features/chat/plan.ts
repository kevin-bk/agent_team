import type { PlanDTO } from "../../api/types";

export type TodoStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "cancelled";

export interface TodoItem {
  id: string;
  content: string;
  status: TodoStatus;
}

export interface PlanSummary {
  total: number;
  pending: number;
  in_progress: number;
  completed: number;
  cancelled: number;
}

export interface Plan {
  todos: TodoItem[];
  summary: PlanSummary;
}

/** Name of the builtin task-list tool (see deep_agent TodoTool). */
export const TODO_TOOL = "todo";

function normStatus(s: unknown): TodoStatus {
  return s === "in_progress" || s === "completed" || s === "cancelled"
    ? s
    : "pending";
}

/** Parse a raw ``todos`` array (from tool input or output) into items. */
function parseTodos(raw: unknown): TodoItem[] | null {
  if (!Array.isArray(raw)) return null;
  const items: TodoItem[] = [];
  for (const r of raw) {
    if (r && typeof r === "object") {
      const o = r as Record<string, unknown>;
      const content = typeof o.content === "string" ? o.content : "";
      if (!content.trim()) continue;
      items.push({
        id: typeof o.id === "string" ? o.id : String(items.length),
        content,
        status: normStatus(o.status),
      });
    }
  }
  return items;
}

function summarize(todos: TodoItem[]): PlanSummary {
  const s: PlanSummary = {
    total: todos.length,
    pending: 0,
    in_progress: 0,
    completed: 0,
    cancelled: 0,
  };
  for (const t of todos) s[t.status] += 1;
  return s;
}

/**
 * Build a {@link Plan} from the authoritative backend snapshot
 * (`GET /api/conversations/:id/todos`). The server owns the merge / replace
 * semantics in its `TodoStore`, so the client just normalizes the shape.
 * Returns `null` when there is no plan yet.
 */
export function planFromApi(dto: PlanDTO | null | undefined): Plan | null {
  const todos = parseTodos(dto?.todos);
  if (!todos || todos.length === 0) return null;
  return { todos, summary: summarize(todos) };
}
