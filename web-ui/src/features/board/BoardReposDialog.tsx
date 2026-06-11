import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { FolderGit2, GitBranch, Plus, X } from "@/components/icons";
import { useBoardRepoMutations, useBoardRepos } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";

/**
 * Assign code repositories (from the central registry) to this board. Every task
 * on the board gets its own working copy of the assigned repos on its first run.
 */
export function BoardReposDialog({
  boardId,
  open,
  onClose,
}: {
  boardId: string;
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const repos = useBoardRepos(open ? boardId : undefined);
  const { assign, unassign } = useBoardRepoMutations(boardId);

  const assigned = repos.data?.assigned ?? [];
  const available = repos.data?.available ?? [];
  const busy = assign.isPending || unassign.isPending;

  const doAssign = async (repoId: string) => {
    try {
      await assign.mutateAsync({ repoId });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to assign");
    }
  };
  const doUnassign = async (repoId: string) => {
    try {
      await unassign.mutateAsync(repoId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove");
    }
  };
  const doSetPush = async (
    repoId: string,
    branchOverride: string | null,
    allowPush: boolean,
  ) => {
    try {
      await assign.mutateAsync({ repoId, branchOverride, allowPush });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Board repositories</DialogTitle>
        </DialogHeader>

        <div className="grid gap-3 pt-1">
          <p className="text-[12.5px] text-muted-foreground">
            Repos assigned here are copied into each task's workspace so agents can
            code in them independently.
          </p>

          {repos.isLoading ? (
            <div className="flex items-center gap-1.5 py-3 text-xs text-muted-foreground">
              <Spinner className="h-3 w-3" /> loading…
            </div>
          ) : (
            <>
              <Section title="Assigned">
                {assigned.length === 0 ? (
                  <Empty>No repositories assigned yet.</Empty>
                ) : (
                  assigned.map(({ repo, branch_override, allow_push }) => (
                    <Row key={repo.id}>
                      <RepoLabel name={repo.name} url={repo.git_url} branch={branch_override || repo.default_branch} />
                      <label
                        className="flex shrink-0 items-center gap-1.5 text-[12px] text-muted-foreground"
                        title={
                          repo.allow_push
                            ? "Allow agents on this board to push this repo"
                            : "Enable push for this repo in its settings first"
                        }
                      >
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 accent-[var(--primary)]"
                          checked={allow_push && repo.allow_push}
                          disabled={busy || !repo.allow_push}
                          onChange={(e) =>
                            doSetPush(repo.id, branch_override, e.target.checked)
                          }
                        />
                        Allow push
                      </label>
                      <Button
                        variant="ghost"
                        size="icon"
                        disabled={busy}
                        onClick={() => doUnassign(repo.id)}
                        title="Remove from board"
                      >
                        <X />
                      </Button>
                    </Row>
                  ))
                )}
              </Section>

              <Section title="Available">
                {available.length === 0 ? (
                  <Empty>
                    No more repositories to add.{" "}
                    <button
                      type="button"
                      className="text-primary hover:underline"
                      onClick={() => {
                        onClose();
                        navigate("/repositories");
                      }}
                    >
                      Manage repositories
                    </button>
                  </Empty>
                ) : (
                  available.map((repo) => (
                    <Row key={repo.id}>
                      <RepoLabel name={repo.name} url={repo.git_url} branch={repo.default_branch} />
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() => doAssign(repo.id)}
                      >
                        <Plus /> Add
                      </Button>
                    </Row>
                  ))
                )}
              </Section>
            </>
          )}
        </div>

        <DialogFooter>
          <button
            type="button"
            className="mr-auto text-[12.5px] text-muted-foreground hover:text-foreground"
            onClick={() => {
              onClose();
              navigate("/repositories");
            }}
          >
            Manage repositories →
          </button>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1">
      <span className="text-[11px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
        {title}
      </span>
      <div className="grid gap-1">{children}</div>
    </div>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 rounded border border-border bg-surface-1 py-1.5 pl-2.5 pr-1.5">
      {children}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="py-1.5 text-[12.5px] text-muted-foreground">{children}</div>;
}

function RepoLabel({
  name,
  url,
  branch,
}: {
  name: string;
  url: string;
  branch: string | null;
}) {
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <FolderGit2 className="h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0">
        <div className="truncate text-[13px] font-medium text-foreground">{name}</div>
        <div className="flex items-center gap-1.5 truncate text-[11.5px] text-muted-foreground">
          <span className="truncate">{url}</span>
          {branch && (
            <span className="inline-flex items-center gap-0.5">
              <GitBranch className="h-3 w-3" />
              {branch}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
