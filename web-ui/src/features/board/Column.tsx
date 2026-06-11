import { autoScrollForElements } from "@atlaskit/pragmatic-drag-and-drop-auto-scroll/element";
import { combine } from "@atlaskit/pragmatic-drag-and-drop/combine";
import {
  dropTargetForElements,
  monitorForElements,
} from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { Plus } from "@/components/icons";
import { useEffect, useRef, useState } from "react";
import type { BoardColumn, BoardMemberDTO, TaskDTO } from "@/api/types";
import { cn } from "@/lib/utils";
import { TaskCard } from "./TaskCard";

export function Column({
  column,
  tasks,
  canEdit,
  membersById,
  compact,
  onTaskClick,
  onEditTask,
  onAddTask,
}: {
  column: BoardColumn;
  tasks: TaskDTO[];
  canEdit: boolean;
  membersById: Map<string, BoardMemberDTO>;
  /** Boards with ≤8 columns shrink to fit; 9+ keep full width and scroll. */
  compact: boolean;
  onTaskClick: (task: TaskDTO) => void;
  onEditTask: (task: TaskDTO) => void;
  onAddTask: (status: string) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const [over, setOver] = useState(false);
  // True while ANY task card is being dragged anywhere on the board — real
  // Jira lights every lane up as soon as a drag starts.
  const [dragActive, setDragActive] = useState(false);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    return combine(
      dropTargetForElements({
        element: el,
        canDrop: ({ source }) => source.data.kind === "task",
        getData: () => ({ kind: "column", status: column.key }),
        onDragEnter: () => setOver(true),
        onDragLeave: () => setOver(false),
        onDrop: () => setOver(false),
      }),
      monitorForElements({
        canMonitor: ({ source }) => source.data.kind === "task",
        onDragStart: () => setDragActive(true),
        onDrop: () => setDragActive(false),
      }),
      autoScrollForElements({ element: el }),
    );
  }, [column.key]);

  // Classic-Jira status lane: flat #F4F5F7 column, plain gray uppercase
  // header ("TO DO 4") — no dots, no count badges, no per-column colour.
  // While dragging (real-Jira UX): every lane tints blue with a strong
  // border, all cards vanish, and the lane name shows centred in the lane;
  // the lane under the pointer goes darker still.
  return (
    <div
      className={cn(
        "relative flex flex-col rounded transition-colors",
        // ≤8 columns: share the row equally and squeeze down to 140px each so
        // everything stays visible. 9+: fixed comfortable width → h-scroll.
        compact
          ? "min-w-[140px] max-w-[340px] flex-1 basis-0"
          : "w-[270px] shrink-0",
        over
          ? "bg-primary/20 ring-2 ring-inset ring-primary"
          : dragActive
            ? "bg-primary/10 ring-1 ring-inset ring-primary/60"
            : "bg-surface-1",
      )}
    >
      <div className="px-3 pb-2.5 pt-3.5">
        <h2 className="truncate text-[12.5px] font-semibold uppercase tracking-[0.02em] text-foreground/75">
          {column.name}
          <span className="ml-1.5 tabular-nums">{tasks.length}</span>
        </h2>
      </div>

      {dragActive && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center p-2">
          <span className="truncate text-[14px] font-semibold uppercase tracking-wide text-primary">
            {column.name}
          </span>
        </div>
      )}

      <div
        ref={listRef}
        className="scrollbar-thin flex min-h-[2.5rem] flex-1 flex-col gap-[5px] overflow-y-auto px-[5px] pb-1"
      >
        {tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            canEdit={canEdit}
            assignee={task.assignee_id ? membersById.get(task.assignee_id) : undefined}
            concealed={dragActive}
            onClick={onTaskClick}
            onEdit={onEditTask}
          />
        ))}
        {tasks.length === 0 && !canEdit && !dragActive && (
          <div className="flex flex-1 items-center justify-center py-8 text-xs text-muted-foreground">
            No tasks
          </div>
        )}
      </div>

      {canEdit && (
        <button
          type="button"
          onClick={() => onAddTask(column.key)}
          className={cn(
            "mx-[5px] mb-1.5 flex items-center gap-1.5 rounded px-2 py-1.5 text-[14px] text-muted-foreground transition-colors duration-100 hover:bg-surface-3 hover:text-foreground",
            dragActive && "invisible",
          )}
        >
          <Plus className="h-4 w-4" /> Create issue
        </button>
      )}
    </div>
  );
}
