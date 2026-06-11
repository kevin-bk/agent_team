import { useState } from "react";
import { toast } from "sonner";
import { useCronMutations } from "@/api/hooks";
import { ApiError, type CronJob } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

type ScheduleKind = "recurring" | "once";

export function CronDialog({
  profile,
  job,
  open,
  onOpenChange,
}: {
  profile: string;
  job?: CronJob;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const editing = !!job;
  const { create, patch } = useCronMutations(profile);
  const [name, setName] = useState(job?.name ?? "");
  const [prompt, setPrompt] = useState(job?.prompt ?? "");
  const [channelId, setChannelId] = useState(job?.deliver.channel_id ?? "");
  const [kind, setKind] = useState<ScheduleKind>(
    job?.run_once ? "once" : "recurring",
  );
  const [schedule, setSchedule] = useState(
    job && !job.run_once ? job.schedule : "0 9 * * *",
  );
  const [runAt, setRunAt] = useState("in 10m");

  const busy = create.isPending || patch.isPending;

  const submit = async () => {
    try {
      if (editing) {
        await patch.mutateAsync({
          jobId: job.id,
          body: {
            name,
            prompt,
            deliver: { platform: "mattermost", channel_id: channelId },
            ...(kind === "recurring" ? { schedule } : {}),
          },
        });
        toast.success("Job updated");
      } else {
        await create.mutateAsync({
          profile,
          name,
          prompt,
          deliver: { platform: "mattermost", channel_id: channelId },
          ...(kind === "recurring"
            ? { schedule }
            : { run_at: runAt }),
        });
        toast.success("Job created");
      }
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save job");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editing ? "Edit job" : "New scheduled job"}</DialogTitle>
          <DialogDescription>
            The agent runs this prompt on schedule and posts the result to the
            chosen channel.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Morning AI news" />
          </Field>
          <Field label="Prompt">
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="Read the news and summarize today's AI developments."
            />
          </Field>
          <Field label="Deliver to (Mattermost channel id)">
            <Input
              value={channelId}
              onChange={(e) => setChannelId(e.target.value)}
              placeholder="channel id"
            />
          </Field>

          {!editing && (
            <div className="flex gap-2 text-sm">
              <TabButton active={kind === "recurring"} onClick={() => setKind("recurring")}>
                Recurring
              </TabButton>
              <TabButton active={kind === "once"} onClick={() => setKind("once")}>
                One-shot
              </TabButton>
            </div>
          )}

          {kind === "recurring" || editing ? (
            <Field label="Schedule (crontab or 'every 5m')">
              <Input
                value={schedule}
                onChange={(e) => setSchedule(e.target.value)}
                placeholder="0 9 * * *"
              />
            </Field>
          ) : (
            <Field label="Run at ('in 10m' or ISO-8601)">
              <Input
                value={runAt}
                onChange={(e) => setRunAt(e.target.value)}
                placeholder="in 10m"
              />
            </Field>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !name || !prompt || !channelId}>
            {editing ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function TabButton({
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
      className={
        active
          ? "rounded-md bg-primary px-3 py-1 text-primary-foreground"
          : "rounded-md border border-border px-3 py-1 text-muted-foreground hover:bg-accent"
      }
    >
      {children}
    </button>
  );
}
