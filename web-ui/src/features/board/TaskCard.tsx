import { combine } from "@atlaskit/pragmatic-drag-and-drop/combine";
import {
  draggable,
  dropTargetForElements,
} from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import {
  attachClosestEdge,
  type Edge,
  extractClosestEdge,
} from "@atlaskit/pragmatic-drag-and-drop-hitbox/closest-edge";
import { Pencil } from "@/components/icons";
import { useEffect, useRef, useState } from "react";
import type { BoardMemberDTO, TaskDTO } from "@/api/types";
import { IssueTypeIcon, JiraAvatar, taskIssueType } from "@/components/jira";
import { cn } from "@/lib/utils";
import { labelClass } from "./labels";
import { PriorityIcon } from "./priority";

export function TaskCard({
  task,
  canEdit,
  assignee,
  concealed,
  onClick,
  onEdit,
}: {
  task: TaskDTO;
  canEdit: boolean;
  assignee?: BoardMemberDTO;
  /** Real-Jira drag UX: all cards vanish while a drag is in flight. */
  concealed?: boolean;
  onClick: (task: TaskDTO) => void;
  onEdit?: (task: TaskDTO) => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const [dragging, setDragging] = useState(false);
  const [edge, setEdge] = useState<Edge | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    return combine(
      draggable({
        element: el,
        canDrag: () => canEdit,
        getInitialData: () => ({
          kind: "task",
          taskId: task.id,
          status: task.status,
        }),
        onDragStart: () => setDragging(true),
        onDrop: () => setDragging(false),
      }),
      dropTargetForElements({
        element: el,
        canDrop: ({ source }) => source.data.kind === "task",
        getIsSticky: () => true,
        getData: ({ input, element }) =>
          attachClosestEdge(
            { kind: "card", taskId: task.id, status: task.status },
            { input, element, allowedEdges: ["top", "bottom"] },
          ),
        onDrag: ({ self, source }) => {
          if (source.data.taskId === task.id) {
            setEdge(null);
            return;
          }
          setEdge(extractClosestEdge(self.data));
        },
        onDragLeave: () => setEdge(null),
        onDrop: () => setEdge(null),
      }),
    );
  }, [task.id, task.status, canEdit]);

  const type = taskIssueType(task);
  const assigneeName =
    assignee?.display_name || assignee?.email || assignee?.user_id;

  // Classic-Jira issue card: a borderless white card floating on the gray
  // lane via a single subtle shadow (no border, no colour stripe). Hover
  // tints the whole card backgroundLight, exactly like the jira-clone demo.
  return (
    // `invisible` (not display:none) keeps layout/native-drag stable while
    // hiding every card during a drag, like real Jira.
    <div className={cn("group/card relative", concealed && "invisible")}>
      {edge === "top" && <DropLine />}
      {canEdit && onEdit && (
        <button
          type="button"
          aria-label="Edit task"
          title="Edit task"
          onClick={(e) => {
            e.stopPropagation();
            onEdit(task);
          }}
          className="absolute right-1.5 top-1.5 z-10 rounded bg-card/90 p-1 text-muted-foreground opacity-0 shadow-raised transition hover:bg-surface-3 hover:text-foreground focus-visible:opacity-100 group-hover/card:opacity-100"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      )}
      <button
        ref={ref}
        type="button"
        onClick={() => onClick(task)}
        className={cn(
          "flex min-h-[110px] w-full select-none flex-col items-stretch rounded border border-border bg-card p-2 text-left shadow-card transition-colors duration-100 hover:bg-surface-1",
          canEdit ? "cursor-grab active:cursor-grabbing" : "cursor-pointer",
          dragging && "opacity-50 shadow-overlay ring-2 ring-primary",
        )}
      >
        {/* margin (not padding) below the clamp: padding inside a -webkit-box
            clamp gets clipped, which both hid the trailing "…" and glued the
            title to the meta row. */}
        <p
          className="mb-2 line-clamp-3 text-[14px] leading-[1.45] text-foreground [overflow-wrap:anywhere]"
          title={task.title}
        >
          {task.title}
        </p>

        {task.labels.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1">
            {task.labels.map((label) => (
              <span
                key={label}
                className={cn(
                  "inline-flex max-w-full items-center truncate rounded-sm px-1.5 py-px text-[10px] font-semibold uppercase tracking-[0.04em]",
                  labelClass(label),
                )}
              >
                {label}
              </span>
            ))}
          </div>
        )}

        {/* Meta row (current-Jira anatomy): type icon + key on the left,
            priority + assignee on the right. mt-auto pins it to the card
            bottom so short cards keep a steady height. */}
        <div className="mt-auto flex items-center justify-between gap-2">
          <div className="flex min-w-0 flex-1 items-center gap-1.5">
            <IssueTypeIcon type={type} size={16} />
            <span
              className="min-w-0 shrink truncate text-[12px] font-medium text-muted-foreground"
              title={task.workspace_path}
            >
              {task.human_key}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <PriorityIcon priority={task.priority} className="h-4 w-4" />
            {assignee && (
              <JiraAvatar
                name={assigneeName}
                src={assignee.avatar_url}
                size={20}
              />
            )}
          </div>
        </div>
      </button>
      {edge === "bottom" && <DropLine />}
    </div>
  );
}

function DropLine() {
  return <div className="my-1 h-0.5 rounded-full bg-primary" />;
}
