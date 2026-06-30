import { FileText, Maximize, Minimize } from "@/components/icons";
import { useEffect, useMemo, useState } from "react";
import type { GitChangeStatus, TaskChangesResponse } from "@/api/types";
import { cn } from "@/lib/utils";
import { STATUS_META } from "./changeMeta";
import type { DraftStore } from "./FileContentViewer";
import { FileTabsPane } from "./FileTabsPane";
import { sortFilesByPriority } from "./filePriority";
import { WorkspaceTree, type TreeMode } from "./WorkspaceTree";

/**
 * The Files browser half of the Code workspace: a left rail (quick-access pills
 * for high-priority changed files + a workspace tree that can scope to the
 * change set) and a right tab pane of open files. Open-file state is lifted to
 * {@link CodeWorkspace} so the Changes view can deep-link a file into a tab.
 */
export function FilesView({
  taskId,
  changes,
  search,
  repoFilter,
  openPaths,
  activePath,
  onOpen,
  onActivate,
  onClose,
  canEdit = false,
  drafts,
}: {
  taskId: string;
  changes: TaskChangesResponse | undefined;
  /** Shared toolbar search (filters tree + pills by path substring). */
  search: string;
  /** Shared repo filter ("all" or a slug). */
  repoFilter: string;
  openPaths: string[];
  activePath: string | null;
  onOpen: (path: string) => void;
  onActivate: (path: string) => void;
  onClose: (path: string) => void;
  canEdit?: boolean;
  drafts?: DraftStore;
}) {
  const { changedPaths, statusByPath } = useMemo(() => {
    const status = new Map<string, GitChangeStatus>();
    const paths: string[] = [];
    for (const f of changes?.files ?? []) {
      if (repoFilter !== "all" && f.repo !== repoFilter) continue;
      const wsPath = `${f.repo}/${f.path}`;
      status.set(wsPath, f.status);
      // Deleted files have nothing to open; keep them out of the openable set.
      if (f.status !== "D") paths.push(wsPath);
    }
    return { changedPaths: paths, statusByPath: status };
  }, [changes?.files, repoFilter]);

  const hasChanges = changedPaths.length > 0;
  const [mode, setMode] = useState<TreeMode>("changed");
  // Auto-follow the data (changed view when there are changes, else all files)
  // until the user explicitly picks a mode.
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!touched) setMode(hasChanges ? "changed" : "all");
  }, [hasChanges, touched]);
  const chooseMode = (m: TreeMode) => {
    setTouched(true);
    setMode(m);
  };

  const pills = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = q
      ? changedPaths.filter((p) => p.toLowerCase().includes(q))
      : changedPaths;
    return sortFilesByPriority(filtered).slice(0, 8);
  }, [changedPaths, search]);

  // Full screen lifts the whole browser (tree + tabs) out of the cramped cockpit
  // layout into a viewport overlay; Esc (or the toolbar button) exits.
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  return (
    <div
      className={cn(
        fullscreen
          ? "fixed inset-0 z-50 flex bg-background"
          : "flex min-h-0 flex-1 overflow-hidden",
      )}
    >
      <aside className="flex min-h-0 w-64 shrink-0 flex-col border-r border-border bg-surface-1">
        {pills.length > 0 && (
          <div className="flex flex-wrap gap-1 border-b border-border p-2">
            {pills.map((path) => {
              const name = path.split("/").pop() ?? path;
              const status = statusByPath.get(path);
              const meta = status ? STATUS_META[status] : null;
              return (
                <button
                  key={path}
                  type="button"
                  onClick={() => onOpen(path)}
                  title={path}
                  className={cn(
                    "inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                    path === activePath
                      ? "border-primary/40 bg-primary/10 text-primary"
                      : "border-border bg-surface-2 text-muted-foreground hover:border-primary/30 hover:text-foreground",
                  )}
                >
                  {meta && (
                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", meta.dot)} />
                  )}
                  <span className="truncate font-mono">{name}</span>
                </button>
              );
            })}
          </div>
        )}

        <div className="flex items-center justify-between gap-1 px-2 py-1.5">
          <ModeToggle mode={mode} setMode={chooseMode} hasChanges={hasChanges} />
          <button
            type="button"
            onClick={() => setFullscreen((v) => !v)}
            title={fullscreen ? "Exit full screen (Esc)" : "Full screen"}
            aria-label={fullscreen ? "Exit full screen" : "Full screen"}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground"
          >
            {fullscreen ? (
              <Minimize className="h-3.5 w-3.5" />
            ) : (
              <Maximize className="h-3.5 w-3.5" />
            )}
          </button>
        </div>

        <WorkspaceTree
          taskId={taskId}
          mode={mode}
          search={search}
          changedPaths={changedPaths}
          statusByPath={statusByPath}
          activePath={activePath}
          onOpen={onOpen}
        />
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <FileTabsPane
          taskId={taskId}
          openPaths={openPaths}
          activePath={activePath}
          onActivate={onActivate}
          onClose={onClose}
          canEdit={canEdit}
          drafts={drafts}
        />
      </div>
    </div>
  );
}

function ModeToggle({
  mode,
  setMode,
  hasChanges,
}: {
  mode: TreeMode;
  setMode: (m: TreeMode) => void;
  hasChanges: boolean;
}) {
  return (
    <div className="inline-flex rounded-md border border-border bg-surface-2 p-0.5 text-[11px]">
      <button
        type="button"
        onClick={() => setMode("changed")}
        disabled={!hasChanges}
        className={cn(
          "rounded px-2 py-0.5 font-medium transition-colors disabled:opacity-40",
          mode === "changed"
            ? "bg-primary/10 text-primary"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        Changed
      </button>
      <button
        type="button"
        onClick={() => setMode("all")}
        className={cn(
          "inline-flex items-center gap-1 rounded px-2 py-0.5 font-medium transition-colors",
          mode === "all"
            ? "bg-primary/10 text-primary"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <FileText className="h-3 w-3" /> All files
      </button>
    </div>
  );
}
