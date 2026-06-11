import {
  CalendarClock,
  Pencil,
  Play,
  Plus,
  Trash2,
} from "@/components/icons";
import { useState } from "react";
import { toast } from "sonner";
import { useCron, useCronMutations } from "@/api/hooks";
import type { CronJob } from "@/api/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { CronDialog } from "./CronDialog";

export function CronPage({ profile }: { profile: string }) {
  const { data: jobs, isLoading, error } = useCron(profile);
  const { patch, remove, runNow } = useCronMutations(profile);
  const confirm = useConfirm();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<CronJob | undefined>();

  const confirmDelete = async (job: CronJob) => {
    const ok = await confirm({
      title: "Delete scheduled job?",
      description: (
        <>
          <span className="font-medium text-foreground">{job.name}</span> will
          be removed permanently. This cannot be undone.
        </>
      ),
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (ok) {
      remove.mutate(job.id, { onSuccess: () => toast.success("Deleted") });
    }
  };

  const openNew = () => {
    setEditing(undefined);
    setDialogOpen(true);
  };
  const openEdit = (job: CronJob) => {
    setEditing(job);
    setDialogOpen(true);
  };

  return (
    <div className="mx-auto flex h-full w-full max-w-4xl flex-col px-6 py-6">
      <header className="mb-5 flex items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300">
          <CalendarClock className="h-5 w-5" />
        </span>
        <div>
          <h1 className="text-lg font-semibold">Scheduled jobs</h1>
          <p className="text-xs text-muted-foreground">Profile: {profile}</p>
        </div>
        <Button className="ml-auto" onClick={openNew}>
          <Plus className="h-4 w-4" /> New job
        </Button>
      </header>

      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> loading…
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          Cron is not enabled for this profile, or it failed to load.
        </div>
      )}

      <div className="flex flex-col gap-2">
        {jobs?.length === 0 && !isLoading && (
          <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            No scheduled jobs yet.
          </div>
        )}
        {jobs?.map((job) => (
          <div
            key={job.id}
            className={cn(
              "flex items-center gap-3 rounded-lg border border-l-[3px] border-border bg-card/60 px-4 py-3 transition-colors hover:bg-card",
              job.last_status === "failed"
                ? "border-l-rose-400"
                : job.enabled
                  ? "border-l-emerald-400"
                  : "border-l-slate-300 dark:border-l-slate-600",
            )}
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-medium">{job.name}</span>
                {job.run_once ? (
                  <Badge variant="secondary">one-shot</Badge>
                ) : (
                  <Badge variant="outline">{job.schedule}</Badge>
                )}
                {job.last_status === "failed" && (
                  <Badge variant="destructive">last failed</Badge>
                )}
              </div>
              <p className="truncate text-xs text-muted-foreground">
                {job.prompt}
              </p>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                next {relativeTime(job.next_run_at_ms)} · fired {job.fire_count}× ·
                → {job.deliver.channel_id}
              </p>
            </div>

            <Switch
              checked={job.enabled}
              onCheckedChange={(enabled) =>
                patch.mutate({ jobId: job.id, body: { enabled } })
              }
              title={job.enabled ? "Enabled" : "Paused"}
            />
            <Button
              variant="ghost"
              size="icon"
              title="Run now"
              onClick={() =>
                runNow.mutate(job.id, {
                  onSuccess: () => toast.success("Triggered"),
                  onError: () => toast.error("Failed to trigger"),
                })
              }
            >
              <Play className="h-4 w-4 text-emerald-500" />
            </Button>
            <Button variant="ghost" size="icon" title="Edit" onClick={() => openEdit(job)}>
              <Pencil className="h-4 w-4 text-sky-500" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              title="Delete"
              onClick={() => confirmDelete(job)}
            >
              <Trash2 className="h-4 w-4 text-destructive" />
            </Button>
          </div>
        ))}
      </div>

      {dialogOpen && (
        <CronDialog
          profile={profile}
          job={editing}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
        />
      )}
    </div>
  );
}
