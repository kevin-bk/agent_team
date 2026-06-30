import {
  ChevronRight,
  File as FileIcon,
  Folder,
} from "@/components/icons";
import { useMemo, useState } from "react";
import { useTaskFileTree } from "@/api/hooks";
import type { GitChangeStatus, WorkspaceFileNode } from "@/api/types";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { STATUS_META } from "./changeMeta";
import { buildSyntheticTree, type SynNode } from "./fileTree";

export type TreeMode = "changed" | "all";

/**
 * File tree for the Files browser. `changed` mode renders a synthetic,
 * fully-expanded tree built purely from the git change set (fast, fully
 * searchable, status dots on every node). `all` mode lazily lists the real
 * workspace via the per-directory API; status dots overlay any changed file.
 */
export function WorkspaceTree({
  taskId,
  mode,
  search,
  changedPaths,
  statusByPath,
  activePath,
  onOpen,
}: {
  taskId: string;
  mode: TreeMode;
  search: string;
  /** Workspace-relative paths (`<repo>/<file>`) from the change set. */
  changedPaths: string[];
  statusByPath: Map<string, GitChangeStatus>;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const q = search.trim().toLowerCase();

  if (mode === "changed") {
    const paths = q
      ? changedPaths.filter((p) => p.toLowerCase().includes(q))
      : changedPaths;
    if (paths.length === 0) {
      return (
        <Empty
          text={
            q
              ? "No changed files match your search."
              : "No changed files on the task branch yet."
          }
        />
      );
    }
    const tree = buildSyntheticTree(paths);
    return (
      <div className="min-h-0 flex-1 overflow-auto p-1.5 scrollbar-thin">
        <SyntheticTree
          nodes={tree}
          depth={0}
          statusByPath={statusByPath}
          activePath={activePath}
          onOpen={onOpen}
        />
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto p-1.5 scrollbar-thin">
      <LazyTree
        taskId={taskId}
        path=""
        depth={0}
        search={q}
        statusByPath={statusByPath}
        activePath={activePath}
        onOpen={onOpen}
      />
    </div>
  );
}

// ── synthetic (changed-only) tree ──────────────────────────────────────────

function SyntheticTree({
  nodes,
  depth,
  statusByPath,
  activePath,
  onOpen,
}: {
  nodes: SynNode[];
  depth: number;
  statusByPath: Map<string, GitChangeStatus>;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  return (
    <ul className="flex flex-col">
      {nodes.map((node) =>
        node.kind === "dir" ? (
          <SyntheticDir
            key={node.path}
            node={node}
            depth={depth}
            statusByPath={statusByPath}
            activePath={activePath}
            onOpen={onOpen}
          />
        ) : (
          <FileRow
            key={node.path}
            name={node.name}
            path={node.path}
            depth={depth}
            status={statusByPath.get(node.path)}
            active={node.path === activePath}
            onOpen={onOpen}
          />
        ),
      )}
    </ul>
  );
}

function SyntheticDir({
  node,
  depth,
  statusByPath,
  activePath,
  onOpen,
}: {
  node: SynNode;
  depth: number;
  statusByPath: Map<string, GitChangeStatus>;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <li>
      <DirRow name={node.name} depth={depth} open={open} onToggle={() => setOpen((v) => !v)} />
      {open && (
        <SyntheticTree
          nodes={node.children}
          depth={depth + 1}
          statusByPath={statusByPath}
          activePath={activePath}
          onOpen={onOpen}
        />
      )}
    </li>
  );
}

// ── lazy (all files) tree ───────────────────────────────────────────────────

function LazyTree({
  taskId,
  path,
  depth,
  search,
  statusByPath,
  activePath,
  onOpen,
}: {
  taskId: string;
  path: string;
  depth: number;
  search: string;
  statusByPath: Map<string, GitChangeStatus>;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const { data, isLoading, isError } = useTaskFileTree(taskId, path);

  const entries = useMemo(() => {
    const list = data?.entries ?? [];
    if (!search) return list;
    // Best-effort within a loaded level: keep dirs (so the user can drill in)
    // and files whose name matches. Deep search isn't possible with the lazy
    // per-directory API — the changed-only view is the fully-searchable path.
    return list.filter(
      (n) => n.kind === "dir" || n.name.toLowerCase().includes(search),
    );
  }, [data?.entries, search]);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-1.5 px-1 py-1.5">
        {["w-3/4", "w-2/3", "w-1/2"].map((w) => (
          <Skeleton key={w} className={`h-4 ${w}`} />
        ))}
      </div>
    );
  }
  if (isError)
    return <p className="px-2 py-1 text-xs text-rose-500">failed to list</p>;
  if (entries.length === 0) {
    if (depth === 0)
      return <Empty text="No files yet — the agent will create them here." />;
    return null;
  }

  return (
    <ul className="flex flex-col">
      {entries.map((node) =>
        node.kind === "dir" ? (
          <LazyDir
            key={node.path}
            taskId={taskId}
            node={node}
            depth={depth}
            search={search}
            statusByPath={statusByPath}
            activePath={activePath}
            onOpen={onOpen}
          />
        ) : (
          <FileRow
            key={node.path}
            name={node.name}
            path={node.path}
            depth={depth}
            status={statusByPath.get(node.path)}
            active={node.path === activePath}
            onOpen={onOpen}
          />
        ),
      )}
    </ul>
  );
}

function LazyDir({
  taskId,
  node,
  depth,
  search,
  statusByPath,
  activePath,
  onOpen,
}: {
  taskId: string;
  node: WorkspaceFileNode;
  depth: number;
  search: string;
  statusByPath: Map<string, GitChangeStatus>;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  // Auto-open while searching so matches deeper in the tree surface as the user
  // drills; otherwise start collapsed.
  const [open, setOpen] = useState(false);
  const expanded = open || !!search;
  return (
    <li>
      <DirRow
        name={node.name}
        depth={depth}
        open={expanded}
        onToggle={() => setOpen((v) => !v)}
      />
      {expanded && (
        <LazyTree
          taskId={taskId}
          path={node.path}
          depth={depth + 1}
          search={search}
          statusByPath={statusByPath}
          activePath={activePath}
          onOpen={onOpen}
        />
      )}
    </li>
  );
}

// ── shared rows ─────────────────────────────────────────────────────────────

function indentStyle(depth: number) {
  return { paddingLeft: `${depth * 12 + 4}px` };
}

function DirRow({
  name,
  depth,
  open,
  onToggle,
}: {
  name: string;
  depth: number;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      style={indentStyle(depth)}
      className="flex w-full items-center gap-1 rounded py-0.5 pr-1 text-left text-xs text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground"
    >
      <ChevronRight
        className={cn("h-3 w-3 shrink-0 transition-transform", open && "rotate-90")}
      />
      <Folder className="h-3.5 w-3.5 shrink-0 text-brand-400" />
      <span className="truncate">{name}</span>
    </button>
  );
}

function FileRow({
  name,
  path,
  depth,
  status,
  active,
  onOpen,
}: {
  name: string;
  path: string;
  depth: number;
  status?: GitChangeStatus;
  active: boolean;
  onOpen: (path: string) => void;
}) {
  const meta = status ? STATUS_META[status] : null;
  return (
    <button
      type="button"
      onClick={() => onOpen(path)}
      style={indentStyle(depth + 1)}
      title={path}
      className={cn(
        "flex w-full items-center gap-1.5 rounded py-0.5 pr-1.5 text-left text-xs transition-colors",
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:bg-surface-3 hover:text-foreground",
      )}
    >
      <FileIcon className="h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {meta && (
        <span
          title={meta.label}
          className={cn("h-1.5 w-1.5 shrink-0 rounded-full", meta.dot)}
        />
      )}
    </button>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <p className="px-2 py-3 text-xs text-muted-foreground">{text}</p>
  );
}
