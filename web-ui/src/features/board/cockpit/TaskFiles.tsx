import {
  ChevronRight,
  File as FileIcon,
  Folder,
  Trash2,
} from "@/components/icons";
import { useState } from "react";
import { toast } from "sonner";
import { useDeleteTaskFile, useTaskFileTree } from "@/api/hooks";
import type { WorkspaceFileNode } from "@/api/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** MIME type the chat composer accepts when a file is dropped from the tree. */
export const ARTIFACT_DND_TYPE = "application/x-task-file";

/**
 * Browser for the task's *shared* `/workspace/<task>` folder. Selecting a file
 * opens it in the large {@link FileViewerModal} (handled by the parent via
 * `onSelect`). Files and folders are draggable into the agent chat composer so
 * the user can hand the agent a workspace path. Editors can delete entries
 * inline (hover row); deleting a folder removes everything inside it.
 */
export function TaskFiles({
  taskId,
  selected,
  onSelect,
  canDelete = false,
  onDeleted,
}: {
  taskId: string;
  selected: string;
  onSelect: (path: string) => void;
  canDelete?: boolean;
  /** Called after a successful delete (e.g. close the viewer if it was open). */
  onDeleted?: (path: string) => void;
}) {
  const del = useDeleteTaskFile(taskId);
  const confirm = useConfirm();

  const handleDelete = async (node: WorkspaceFileNode) => {
    const isDir = node.kind === "dir";
    const ok = await confirm({
      title: isDir ? "Delete folder?" : "Delete file?",
      description: isDir
        ? `“${node.name}” and everything inside it will be permanently removed from the workspace.`
        : `“${node.name}” will be permanently removed from the workspace.`,
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!ok) return;
    try {
      await del.mutateAsync(node.path);
      onDeleted?.(node.path);
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : `Failed to delete ${isDir ? "folder" : "file"}`,
      );
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-auto p-2 scrollbar-thin">
      <FileTree
        taskId={taskId}
        path=""
        selected={selected}
        onSelect={onSelect}
        onDelete={canDelete ? handleDelete : undefined}
      />
    </div>
  );
}

function FileTree({
  taskId,
  path,
  selected,
  onSelect,
  onDelete,
}: {
  taskId: string;
  path: string;
  selected: string;
  onSelect: (p: string) => void;
  onDelete?: (node: WorkspaceFileNode) => void;
}) {
  const { data, isLoading, isError } = useTaskFileTree(taskId, path);
  if (isLoading) {
    const widths = ["w-3/4", "w-2/3", "w-1/2", "w-3/5", "w-2/5"];
    return (
      <div className="flex flex-col gap-1.5 px-1 py-1.5">
        {widths.map((w) => (
          <Skeleton key={w} className={`h-4 ${w}`} />
        ))}
      </div>
    );
  }
  if (isError)
    return <div className="px-1 py-1 text-xs text-destructive">failed to list</div>;
  const entries = data?.entries ?? [];
  if (entries.length === 0)
    return (
      <div className="px-1 py-2 text-xs text-slate-400 dark:text-muted-foreground">
        No files yet — the agent will create them here.
      </div>
    );
  return (
    <ul className="flex flex-col">
      {entries.map((node) => (
        <TreeNode
          key={node.path}
          taskId={taskId}
          node={node}
          selected={selected}
          onSelect={onSelect}
          onDelete={onDelete}
        />
      ))}
    </ul>
  );
}

function TreeNode({
  taskId,
  node,
  selected,
  onSelect,
  onDelete,
}: {
  taskId: string;
  node: WorkspaceFileNode;
  selected: string;
  onSelect: (p: string) => void;
  onDelete?: (node: WorkspaceFileNode) => void;
}) {
  const [open, setOpen] = useState(false);
  if (node.kind === "dir") {
    return (
      <li>
        <div className="group/file flex items-center">
          <button
            type="button"
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(ARTIFACT_DND_TYPE, node.path);
              e.dataTransfer.setData("text/plain", node.path);
              e.dataTransfer.effectAllowed = "copy";
            }}
            onClick={() => setOpen((v) => !v)}
            title={`${node.name} · drag into chat to share its path`}
            className="flex min-w-0 flex-1 items-center gap-1 rounded px-1 py-0.5 text-left text-xs text-slate-500 hover:bg-slate-100 hover:text-slate-800 active:cursor-grabbing dark:text-muted-foreground dark:hover:bg-surface-3"
          >
            <ChevronRight
              className={cn("h-3 w-3 shrink-0 transition-transform", open && "rotate-90")}
            />
            <Folder className="h-3 w-3 shrink-0 text-brand-400" />
            <span className="truncate">{node.name}</span>
          </button>
          {onDelete && (
            <button
              type="button"
              aria-label={`Delete ${node.name}`}
              title={`Delete ${node.name} and its contents`}
              onClick={() => onDelete(node)}
              className="ml-0.5 hidden shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive group-hover/file:block"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          )}
        </div>
        {open && (
          <div className="ml-3 border-l border-slate-200 pl-1 dark:border-border">
            <FileTree
              taskId={taskId}
              path={node.path}
              selected={selected}
              onSelect={onSelect}
              onDelete={onDelete}
            />
          </div>
        )}
      </li>
    );
  }
  return (
    <li className="group/file flex items-center">
      <button
        type="button"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData(ARTIFACT_DND_TYPE, node.path);
          e.dataTransfer.setData("text/plain", node.path);
          e.dataTransfer.effectAllowed = "copy";
        }}
        onClick={() => onSelect(node.path)}
        title={`Open ${node.name} · drag into chat to share its path`}
        className={cn(
          "flex min-w-0 flex-1 cursor-pointer items-center gap-1 rounded px-1 py-0.5 text-left text-xs hover:bg-slate-100 active:cursor-grabbing dark:hover:bg-surface-3",
          selected === node.path
            ? "bg-brand-50 text-brand-700 dark:bg-surface-3 dark:text-foreground"
            : "text-slate-500 hover:text-slate-800 dark:text-muted-foreground",
        )}
      >
        <FileIcon className="ml-4 h-3 w-3 shrink-0" />
        <span className="truncate">{node.name}</span>
      </button>
      {onDelete && (
        <button
          type="button"
          aria-label={`Delete ${node.name}`}
          title={`Delete ${node.name}`}
          onClick={() => onDelete(node)}
          className="ml-0.5 hidden shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive group-hover/file:block"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      )}
    </li>
  );
}
