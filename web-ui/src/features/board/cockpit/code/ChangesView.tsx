import { FileDiff, GitBranch } from "@/components/icons";
import { useMemo } from "react";
import type { TaskChangeEntry, TaskChangesResponse } from "@/api/types";
import { DiffStatBadge } from "@/components/DiffView";
import { Spinner } from "@/components/ui/spinner";
import { FileDiffCard } from "./FileDiffCard";

/**
 * The git-truth "Changes" surface: every file the task changed vs its base
 * branch, grouped by repo, each a lazily-loaded collapsible diff. This reflects
 * the on-disk end state (commits + uncommitted + untracked), so it spans every
 * agent / run / direct-CLI push — not just one conversation's tool calls.
 */
export function ChangesView({
  taskId,
  data,
  isLoading,
  isError,
  filter,
  repoFilter,
  onOpenFile,
}: {
  taskId: string;
  data: TaskChangesResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  /** Case-insensitive path substring filter from the toolbar search box. */
  filter: string;
  /** Repo slug to show, or "all". */
  repoFilter: string;
  onOpenFile?: (repo: string, path: string) => void;
}) {
  const repos = data?.repos ?? [];
  const multiRepo = repos.length > 1;

  const files = useMemo(() => {
    const f = (data?.files ?? []).filter(
      (x) => repoFilter === "all" || x.repo === repoFilter,
    );
    const q = filter.trim().toLowerCase();
    return q ? f.filter((x) => x.path.toLowerCase().includes(q)) : f;
  }, [data?.files, repoFilter, filter]);

  const grouped = useMemo(() => {
    const by = new Map<string, TaskChangeEntry[]>();
    for (const f of files) {
      const list = by.get(f.repo);
      if (list) list.push(f);
      else by.set(f.repo, [f]);
    }
    return by;
  }, [files]);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <Spinner className="h-5 w-5 text-brand-400" /> Loading changes…
      </div>
    );
  }
  if (isError) {
    return <Centered text="Failed to load changes." tone="error" />;
  }
  if (repos.length === 0) {
    return (
      <Centered
        icon
        text="This task has no code repo assigned. Assign one in Board settings to review git changes here — the per-thread Changes tab still works in the meantime."
      />
    );
  }
  if (!repos.some((r) => r.present)) {
    return (
      <Centered
        icon
        text="Workspace not prepared yet. Run an agent on this task (or “Prepare workspace”) to create the repo working copies."
      />
    );
  }
  if (files.length === 0) {
    const anyChanges = (data?.files ?? []).length > 0;
    return (
      <Centered
        icon
        text={
          anyChanges
            ? "No files match your filter."
            : "No changes yet — the agent hasn’t modified any tracked files on its task branch."
        }
      />
    );
  }

  const totalAdded = files.reduce((n, f) => n + f.additions, 0);
  const totalRemoved = files.reduce((n, f) => n + f.deletions, 0);

  return (
    <div className="min-h-0 flex-1 overflow-auto px-3 py-3 scrollbar-thin">
      <div className="mx-auto max-w-4xl space-y-4">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-semibold text-foreground">
            {files.length} file{files.length === 1 ? "" : "s"} changed
          </span>
          <DiffStatBadge added={totalAdded} removed={totalRemoved} />
          {data?.truncated && (
            <span className="text-amber-600 dark:text-amber-400">
              · list truncated
            </span>
          )}
        </div>

        {[...grouped.entries()].map(([repo, entries]) => {
          const meta = repos.find((r) => r.slug === repo);
          return (
            <div key={repo} className="space-y-2">
              {multiRepo && (
                <div className="flex items-center gap-1.5 px-0.5 text-[11px] font-medium text-muted-foreground">
                  <GitBranch className="h-3.5 w-3.5" />
                  <span className="font-mono text-foreground">{repo}</span>
                  {meta?.branch && (
                    <span className="font-mono">· {meta.branch}</span>
                  )}
                  <span className="ml-1 rounded-full bg-surface-3 px-1.5 tabular-nums">
                    {entries.length}
                  </span>
                </div>
              )}
              {entries.map((entry) => (
                <FileDiffCard
                  key={`${entry.repo}:${entry.path}`}
                  taskId={taskId}
                  entry={entry}
                  showRepo={false}
                  onOpenFile={onOpenFile}
                />
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Centered({
  text,
  icon = false,
  tone = "muted",
}: {
  text: string;
  icon?: boolean;
  tone?: "muted" | "error";
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
      {icon && (
        <span className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <FileDiff className="h-6 w-6" />
        </span>
      )}
      <p
        className={
          tone === "error"
            ? "max-w-md text-sm text-rose-500"
            : "max-w-md text-sm text-muted-foreground"
        }
      >
        {text}
      </p>
    </div>
  );
}
