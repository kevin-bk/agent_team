import { useState } from "react";
import { toast } from "sonner";
import {
  Download,
  FolderGit2,
  GitBranch,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
} from "@/components/icons";
import { useMe, useRepoMutations, useRepos } from "@/api/hooks";
import type { RepoCloneStatus, RepoDTO } from "@/api/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { RepoDialog } from "./RepoDialog";
import { detectGitPlatform } from "./gitPlatform";

const CLONE_BADGE: Record<RepoCloneStatus, { label: string; variant: "default" | "success" | "destructive" | "outline" }> = {
  absent: { label: "Not cloned", variant: "outline" },
  cloning: { label: "Cloning…", variant: "default" },
  cloned: { label: "Cloned", variant: "success" },
  error: { label: "Sync failed", variant: "destructive" },
};

function scheduleLabel(repo: RepoDTO): string {
  if (repo.schedule_mode === "off") return "Manual";
  if (repo.schedule_mode === "cron") return `Cron: ${repo.schedule_cron ?? ""}`;
  const h = repo.schedule_interval_seconds / 3600;
  if (h >= 1) return `Every ${h}h`;
  return `Every ${Math.round(repo.schedule_interval_seconds / 60)}m`;
}

function relTime(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const m = Math.round(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

/** Admin-only registry of code repositories: add, clone, scheduled pull, edit. */
export function ReposPage() {
  const me = useMe();
  const repos = useRepos();
  const { remove, pull } = useRepoMutations();
  const confirm = useConfirm();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<RepoDTO | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  if (me.data && !me.data.is_admin) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Repository management is available to administrators only.
      </div>
    );
  }

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };
  const openEdit = (repo: RepoDTO) => {
    setEditing(repo);
    setDialogOpen(true);
  };

  // One action: clone if the canonical copy is missing, otherwise pull. The
  // backend's sync endpoint already does clone-or-pull, so a single button is
  // all the user needs.
  const doSync = async (repo: RepoDTO) => {
    const firstClone = repo.clone_status !== "cloned";
    setBusyId(repo.id);
    try {
      const res = await pull.mutateAsync(repo.id);
      if (res.ok) {
        toast.success(firstClone ? "Repository cloned" : "Repository updated");
      } else {
        toast.error(res.message || "Sync failed");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setBusyId(null);
    }
  };

  const doDelete = async (repo: RepoDTO) => {
    const ok = await confirm({
      title: `Delete “${repo.name}”?`,
      description:
        "This removes the repository and its canonical clone. Tasks won't get a copy of it anymore.",
      tone: "danger",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(repo.id);
      toast.success("Repository deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete");
    }
  };

  const list = repos.data ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <div className="flex items-center gap-2.5">
          <FolderGit2 className="h-5 w-5 text-muted-foreground" />
          <div>
            <h1 className="text-[18px] font-semibold text-foreground">Repositories</h1>
            <p className="text-[12.5px] text-muted-foreground">
              Code repos that boards can assign to their tasks.
            </p>
          </div>
        </div>
        <Button onClick={openCreate}>
          <Plus /> Add repository
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        {repos.isLoading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Spinner className="h-4 w-4" /> Loading…
          </div>
        ) : list.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center">
            <FolderGit2 className="mx-auto h-8 w-8 text-muted-foreground/50" />
            <p className="mt-3 text-sm font-medium text-foreground">No repositories yet</p>
            <p className="mt-1 text-[13px] text-muted-foreground">
              Add a repository to clone it and make it available to your boards.
            </p>
            <Button className="mt-4" onClick={openCreate}>
              <Plus /> Add repository
            </Button>
          </div>
        ) : (
          <div className="grid gap-2">
            {list.map((repo) => {
              const badge = CLONE_BADGE[repo.clone_status];
              const busy = busyId === repo.id;
              const platform = detectGitPlatform(repo.git_url);
              const PlatformIcon = platform?.Icon;
              const cloned = repo.clone_status === "cloned";
              return (
                <div
                  key={repo.id}
                  className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3"
                >
                  <div
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-surface-1"
                    title={platform?.name ?? "Git"}
                  >
                    {PlatformIcon ? (
                      <PlatformIcon
                        className="h-5 w-5"
                        style={platform?.color ? { color: platform.color } : undefined}
                      />
                    ) : (
                      <FolderGit2 className="h-5 w-5 text-muted-foreground" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[14px] font-medium text-foreground">
                        {repo.name}
                      </span>
                      <Badge variant={badge.variant}>{badge.label}</Badge>
                      {repo.has_secret && (
                        <Badge variant="outline">{repo.auth_type === "ssh" ? "SSH" : "Token"}</Badge>
                      )}
                      {repo.allow_push && <Badge variant="outline">Push</Badge>}
                    </div>
                    <div className="mt-0.5 truncate text-[12.5px] text-muted-foreground">
                      {repo.git_url}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12px] text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        <GitBranch className="h-3.5 w-3.5" />
                        {repo.default_branch || "default"}
                      </span>
                      <span>{scheduleLabel(repo)}</span>
                      <span>Synced {relTime(repo.last_synced_at)}</span>
                      <span>
                        {repo.used_by_boards} board{repo.used_by_boards === 1 ? "" : "s"}
                      </span>
                      {repo.last_sync_status === "failed" && repo.last_sync_error && (
                        <span className="text-destructive" title={repo.last_sync_error}>
                          last sync failed
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => doSync(repo)}
                      disabled={busy}
                      title={cloned ? "Pull latest changes" : "Clone repository"}
                    >
                      {busy ? (
                        <Loader2 className="animate-spin" />
                      ) : cloned ? (
                        <RefreshCw />
                      ) : (
                        <Download />
                      )}
                      {cloned ? "Sync" : "Clone"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => openEdit(repo)}
                      title="Edit"
                    >
                      <Pencil />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => doDelete(repo)}
                      title="Delete"
                    >
                      <Trash2 />
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <RepoDialog repo={editing} open={dialogOpen} onClose={() => setDialogOpen(false)} />
    </div>
  );
}
