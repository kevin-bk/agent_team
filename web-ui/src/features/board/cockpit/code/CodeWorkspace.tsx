import { FileDiff, FileText, RefreshCw, Search } from "@/components/icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { qk, useTaskChanges } from "@/api/hooks";
import { DiffStatBadge } from "@/components/DiffView";
import { SelectMenu } from "@/components/ui/select-menu";
import { cn } from "@/lib/utils";
import { ChangesView } from "./ChangesView";
import { FilesView } from "./FilesView";

type View = "changes" | "files";

/**
 * Full-width code review surface for a task (the "Code" thread). A segmented
 * Changes/Files toggle switches between the git-truth diff list and a file
 * browser with a tabbed viewer. Open-file state is lifted here so the Changes
 * view (and inbound `openRequest`s, e.g. from the Artifacts panel) can deep-link
 * a file straight into a Files tab.
 */
export function CodeWorkspace({
  taskId,
  openRequest,
}: {
  taskId: string;
  /** Bump `seq` to open `path` (workspace-relative) in a Files tab. */
  openRequest?: { path: string; seq: number };
}) {
  const changes = useTaskChanges(taskId);
  const qc = useQueryClient();
  const [view, setView] = useState<View>("changes");
  const [search, setSearch] = useState("");
  const [repoFilter, setRepoFilter] = useState("all");

  const [openPaths, setOpenPaths] = useState<string[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);

  const openFile = useCallback((path: string) => {
    setOpenPaths((prev) => (prev.includes(path) ? prev : [...prev, path]));
    setActivePath(path);
    setView("files");
  }, []);

  const closeFile = useCallback(
    (path: string) => {
      setOpenPaths((prev) => {
        const idx = prev.indexOf(path);
        if (idx === -1) return prev;
        const next = prev.filter((p) => p !== path);
        setActivePath((cur) =>
          cur !== path ? cur : (next[idx] ?? next[idx - 1] ?? null),
        );
        return next;
      });
    },
    [],
  );

  // Inbound deep-link (Artifacts panel → open in this workspace).
  useEffect(() => {
    if (openRequest?.path) openFile(openRequest.path);
    // Only react to a new request (seq), not to openFile identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRequest?.seq]);

  const data = changes.data;
  const repos = data?.repos ?? [];
  const multiRepo = repos.length > 1;

  const totals = useMemo(() => {
    const files = (data?.files ?? []).filter(
      (f) => repoFilter === "all" || f.repo === repoFilter,
    );
    return {
      count: files.length,
      added: files.reduce((n, f) => n + f.additions, 0),
      removed: files.reduce((n, f) => n + f.deletions, 0),
    };
  }, [data?.files, repoFilter]);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: qk.taskChanges(taskId) });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <div className="inline-flex rounded-md border border-border bg-surface-2 p-0.5">
          <ViewTab active={view === "changes"} onClick={() => setView("changes")}>
            <FileDiff className="h-3.5 w-3.5" /> Changes
            {totals.count > 0 && (
              <span className="rounded-full bg-surface-3 px-1.5 text-[10px] tabular-nums">
                {totals.count}
              </span>
            )}
          </ViewTab>
          <ViewTab active={view === "files"} onClick={() => setView("files")}>
            <FileText className="h-3.5 w-3.5" /> Files
          </ViewTab>
        </div>

        {multiRepo && (
          <SelectMenu
            value={repoFilter}
            onChange={setRepoFilter}
            options={[
              { value: "all", label: "All repos" },
              ...repos.map((r) => ({ value: r.slug, label: r.slug })),
            ]}
            className="w-40"
          />
        )}

        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={view === "changes" ? "Filter changed files…" : "Filter files…"}
            className="h-8 w-full rounded-md border border-input bg-card pl-7 pr-2 text-xs text-foreground placeholder:text-muted-foreground/60 focus:border-ring focus:outline-none"
          />
        </div>

        <div className="ml-auto flex items-center gap-2">
          {view === "changes" && totals.count > 0 && (
            <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <span className="tabular-nums">
                {totals.count} file{totals.count === 1 ? "" : "s"}
              </span>
              <DiffStatBadge added={totals.added} removed={totals.removed} />
            </span>
          )}
          <button
            type="button"
            onClick={refresh}
            aria-label="Refresh changes"
            title="Refresh changes"
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-surface-1 hover:text-foreground"
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", changes.isFetching && "animate-spin")}
            />
          </button>
        </div>
      </div>

      {view === "changes" ? (
        <ChangesView
          taskId={taskId}
          data={data}
          isLoading={changes.isLoading}
          isError={changes.isError}
          filter={search}
          repoFilter={repoFilter}
          onOpenFile={(repo, path) => openFile(`${repo}/${path}`)}
        />
      ) : (
        <FilesView
          taskId={taskId}
          changes={data}
          search={search}
          repoFilter={repoFilter}
          openPaths={openPaths}
          activePath={activePath}
          onOpen={openFile}
          onActivate={setActivePath}
          onClose={closeFile}
        />
      )}
    </div>
  );
}

function ViewTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
