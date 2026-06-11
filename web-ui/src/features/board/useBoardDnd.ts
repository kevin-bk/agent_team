import { monitorForElements } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { extractClosestEdge } from "@atlaskit/pragmatic-drag-and-drop-hitbox/closest-edge";
import { type MutableRefObject, useEffect } from "react";
import type { TaskDTO } from "@/api/types";
import { computePosition, tasksInColumn } from "./reorder";

export interface MoveArgs {
  taskId: string;
  status: string;
  position: number;
}

/**
 * Board-level PDND monitor (plan 16 §04). Reads the latest tasks + move
 * callback from refs so it subscribes exactly once. On drop it resolves the
 * destination column (from the card or column drop target), computes the
 * fractional insert position, and fires `onMoveRef.current` (a no-op if the
 * task didn't actually change column/position).
 */
export function useBoardDnd(
  tasksRef: MutableRefObject<TaskDTO[]>,
  onMoveRef: MutableRefObject<(args: MoveArgs) => void>,
): void {
  useEffect(
    () =>
      monitorForElements({
        canMonitor: ({ source }) => source.data.kind === "task",
        onDrop({ source, location }) {
          const taskId = source.data.taskId as string | undefined;
          const targets = location.current.dropTargets;
          if (!taskId || targets.length === 0) return;

          // Drop targets come innermost-first: a card (if hovered) then its
          // column. Either alone tells us the destination status.
          const cardTarget = targets.find((t) => t.data.kind === "card");
          const columnTarget = targets.find((t) => t.data.kind === "column");
          const targetStatus =
            (cardTarget?.data.status as string | undefined) ??
            (columnTarget?.data.status as string | undefined);
          if (!targetStatus) return;

          const tasks = tasksRef.current;
          const column = tasksInColumn(tasks, targetStatus).filter(
            (t) => t.id !== taskId,
          );

          let insertIndex = column.length; // default: append to the column end
          if (cardTarget) {
            const overId = cardTarget.data.taskId as string;
            const overIdx = column.findIndex((t) => t.id === overId);
            if (overIdx !== -1) {
              const edge = extractClosestEdge(cardTarget.data);
              insertIndex = edge === "bottom" ? overIdx + 1 : overIdx;
            }
          }

          const position = computePosition(column, insertIndex);
          const current = tasks.find((t) => t.id === taskId);
          if (
            current &&
            current.status === targetStatus &&
            current.position === position
          ) {
            return;
          }
          onMoveRef.current({ taskId, status: targetStatus, position });
        },
      }),
    [tasksRef, onMoveRef],
  );
}
