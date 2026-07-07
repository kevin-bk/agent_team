import type { TaskDTO, TaskPriority } from "@/api/types";

const PRIORITY_RANK: Record<string, number> = {
  highest: 0,
  high: 1,
  medium: 2,
  low: 3,
  lowest: 4,
};
const NO_PRIORITY = 5;

function priorityRank(p: TaskPriority | null | undefined): number {
  return p ? (PRIORITY_RANK[p] ?? NO_PRIORITY) : NO_PRIORITY;
}

/**
 * Fractional positioning (plan 16 §04.1). Given the tasks already in the
 * target column (sorted ascending by `position`, with the dragged task
 * excluded) and the index it should land at, return a `position` that slots
 * it there without renumbering the whole column.
 *
 * - empty column        → 1
 * - dropped at the top   → first - 1
 * - dropped at the end   → last + 1
 * - dropped in between    → midpoint of the two neighbours
 */
export function computePosition(
  columnTasks: TaskDTO[],
  insertIndex: number,
): number {
  if (columnTasks.length === 0) return 1;

  const clamped = Math.max(0, Math.min(insertIndex, columnTasks.length));
  if (clamped <= 0) return columnTasks[0].position - 1;
  if (clamped >= columnTasks.length) {
    return columnTasks[columnTasks.length - 1].position + 1;
  }
  const before = columnTasks[clamped - 1].position;
  const after = columnTasks[clamped].position;
  return (before + after) / 2;
}

/**
 * Tasks of one column, sorted by priority (highest first), then by position
 * within the same priority tier. Tasks without a priority sort last.
 */
export function tasksInColumn(tasks: TaskDTO[], status: string): TaskDTO[] {
  return tasks
    .filter((t) => t.status === status)
    .sort((a, b) => {
      const pa = priorityRank(a.priority);
      const pb = priorityRank(b.priority);
      if (pa !== pb) return pa - pb;
      return a.position - b.position || a.created_at.localeCompare(b.created_at);
    });
}
