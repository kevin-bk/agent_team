import type { TaskDTO } from "@/api/types";

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

/** Tasks of one column, sorted ascending by position (stable on ties). */
export function tasksInColumn(tasks: TaskDTO[], status: string): TaskDTO[] {
  return tasks
    .filter((t) => t.status === status)
    .sort((a, b) => a.position - b.position || a.created_at.localeCompare(b.created_at));
}
