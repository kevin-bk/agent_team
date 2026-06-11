import { toast } from "sonner";
import { Check, FolderGit2, Loader2 } from "@/components/icons";
import { usePrepareTaskRepos, useTaskRepos } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * "Code workspace" card in the task cockpit: lists the repos assigned to the
 * board that get copied into this task, whether each copy is present yet, and a
 * button to prepare them on demand (agents also prepare them automatically on
 * their first run). Clicking a present repo opens its folder in the file viewer.
 */
export function TaskRepoCard({
  taskId,
  canEdit,
  onOpenPath,
}: {
  taskId: string;
  canEdit: boolean;
  onOpenPath: (path: string) => void;
}) {
  const repos = useTaskRepos(taskId);
  const prepare = usePrepareTaskRepos(taskId);

  const list = repos.data ?? [];
  if (repos.isLoading || list.length === 0) return null;

  const missing = list.some((r) => !r.present);

  const doPrepare = async () => {
    try {
      await prepare.mutateAsync();
      toast.success("Workspace prepared");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to prepare");
    }
  };

  return (
    <div className="border-b border-border">
      <div className="flex items-center gap-2 px-4 py-2.5">
        <FolderGit2 className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-[13px] font-semibold text-foreground">Code workspace</span>
        {canEdit && missing && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto"
            onClick={doPrepare}
            disabled={prepare.isPending}
          >
            {prepare.isPending ? <Loader2 className="animate-spin" /> : null}
            Prepare
          </Button>
        )}
      </div>
      <div className="grid gap-1 px-3 pb-3">
        {list.map((repo) => (
          <button
            key={repo.slug}
            type="button"
            disabled={!repo.present}
            onClick={() => repo.present && onOpenPath(repo.path)}
            className={cn(
              "flex items-center gap-2 rounded px-2 py-1.5 text-left transition-colors",
              repo.present
                ? "hover:bg-surface-1"
                : "cursor-default opacity-60",
            )}
            title={repo.present ? "Open folder" : "Not prepared yet"}
          >
            <FolderGit2 className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">
              {repo.slug}
            </span>
            {repo.present ? (
              <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                <Check className="h-3.5 w-3.5" /> ready
              </span>
            ) : (
              <span className="text-[11px] text-muted-foreground">not prepared</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
