import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useAgents,
  useCliTargets,
  useTaskSchedule,
  useTaskScheduleHistory,
  useUpdateTaskSchedule,
} from "@/api/hooks";
import type {
  BoardDTO,
  PatchTaskScheduleBody,
  TaskDTO,
  TaskScheduleConversationMode,
} from "@/api/types";
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
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

/**
 * Configure a task's recurring run schedule: at each cron time the chosen agent
 * is sent the opening prompt, either starting a fresh conversation or appending
 * to its existing thread. Scheduled runs never move the task between columns.
 */
export function TaskScheduleDialog({
  board,
  task,
  open,
  onClose,
}: {
  board: BoardDTO;
  task: TaskDTO;
  open: boolean;
  onClose: () => void;
}) {
  const query = useTaskSchedule(open ? task.id : undefined);
  const history = useTaskScheduleHistory(open ? task.id : undefined, open);
  const update = useUpdateTaskSchedule(task.id);
  const agents = useAgents();
  const cliTargets = useCliTargets();

  const [form, setForm] = useState<PatchTaskScheduleBody>({});

  // Agents/CLIs staffed on this board — the only ones a schedule can run.
  const staffedAgents = useMemo(() => {
    const out: { id: string; label: string }[] = [];
    const enabledAgents = new Set(board.agent_ids ?? []);
    const enabledClis = new Set(board.cli_target_ids ?? []);
    for (const a of agents.data ?? [])
      if (enabledAgents.has(a.id)) out.push({ id: a.id, label: a.display_name });
    for (const t of cliTargets.data ?? [])
      if (enabledClis.has(t.id))
        out.push({ id: t.id, label: `${t.label} (direct)` });
    return out;
  }, [board.agent_ids, board.cli_target_ids, agents.data, cliTargets.data]);

  useEffect(() => {
    if (open && query.data) {
      const d = query.data;
      setForm({
        enabled: d.enabled,
        cron: d.cron ?? "",
        timezone: d.timezone,
        agent_alias: d.agent_alias ?? "",
        prompt: d.prompt ?? "",
        conversation_mode: d.conversation_mode,
      });
    }
  }, [open, query.data]);

  const set = <K extends keyof PatchTaskScheduleBody>(
    key: K,
    value: PatchTaskScheduleBody[K],
  ) => setForm((f) => ({ ...f, [key]: value }));

  const mode = (form.conversation_mode ?? "continue") as TaskScheduleConversationMode;

  const save = async () => {
    try {
      await update.mutateAsync(form);
      toast.success("Schedule updated");
      onClose();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to update schedule",
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="w-[92vw] max-w-xl">
        <DialogHeader>
          <DialogTitle>Schedule recurring runs</DialogTitle>
          <DialogDescription>
            On a cron schedule, send the chosen agent an opening message to work
            on this task. Scheduled runs never move the card between columns.
          </DialogDescription>
        </DialogHeader>

        {query.isLoading ? (
          <div className="flex items-center gap-1.5 py-6 text-xs text-muted-foreground">
            <Spinner className="h-3 w-3" /> loading…
          </div>
        ) : (
          <div className="flex max-h-[72vh] flex-col gap-4 overflow-y-auto pr-1">
            <label className="flex items-center justify-between gap-3 rounded-md bg-surface-1 px-3 py-2">
              <span className="text-[13px] font-medium text-foreground">
                Enable schedule for this task
              </span>
              <Switch
                checked={!!form.enabled}
                onCheckedChange={(v) => set("enabled", v)}
              />
            </label>

            <Section title="When">
              <div className="grid grid-cols-2 gap-3">
                <Field label="Cron expression">
                  <Input
                    value={form.cron ?? ""}
                    onChange={(e) => set("cron", e.target.value)}
                    placeholder="0 9 * * *"
                  />
                </Field>
                <Field label="Timezone">
                  <Input
                    value={form.timezone ?? "UTC"}
                    onChange={(e) => set("timezone", e.target.value)}
                    placeholder="UTC"
                  />
                </Field>
              </div>
              <p className="text-[12px] text-muted-foreground">
                Five-field cron, e.g. <code>0 9 * * *</code> = every day at 09:00,{" "}
                <code>0 9 * * 1</code> = every Monday at 09:00.
              </p>
            </Section>

            <Section title="Agent">
              {staffedAgents.length === 0 ? (
                <span className="text-[12px] text-muted-foreground">
                  Staff agents on this board (Board settings → Agents) to pick
                  one for the schedule.
                </span>
              ) : (
                <select
                  value={form.agent_alias ?? ""}
                  onChange={(e) => set("agent_alias", e.target.value)}
                  className="h-9 rounded-md border border-border bg-surface-1 px-2 text-[13px] text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">— pick an agent —</option>
                  {staffedAgents.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.label}
                    </option>
                  ))}
                </select>
              )}
            </Section>

            <Section title="Each run">
              <div className="flex gap-2 text-sm">
                {(["continue", "new"] as const).map((m) => (
                  <TabButton
                    key={m}
                    active={mode === m}
                    onClick={() => set("conversation_mode", m)}
                  >
                    {m === "continue" ? "Continue thread" : "New conversation"}
                  </TabButton>
                ))}
              </div>
              <span className="text-[12px] text-muted-foreground">
                {mode === "continue"
                  ? "Each run appends to the agent's existing conversation."
                  : "Each run starts a fresh conversation (the previous one is archived in History)."}
              </span>
              <Field label="Opening message">
                <Textarea
                  rows={4}
                  value={form.prompt ?? ""}
                  onChange={(e) => set("prompt", e.target.value)}
                  placeholder="e.g. Generate today's daily report and post it as a comment."
                />
              </Field>
              <span className="text-[12px] text-muted-foreground">
                Sent to the agent on every fire. Leave empty to use the default
                (work on the task; read <code>.agent-team/TASK.md</code>).
              </span>
            </Section>

            {query.data?.next_run_at && form.enabled && (
              <span className="text-[12px] text-muted-foreground">
                Next run: {new Date(query.data.next_run_at).toLocaleString()}
              </span>
            )}

            <Section title="History">
              {history.isLoading ? (
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Spinner className="h-3 w-3" /> loading…
                </div>
              ) : (history.data ?? []).length === 0 ? (
                <span className="text-[12px] text-muted-foreground">
                  No scheduled runs yet.
                </span>
              ) : (
                <ul className="flex flex-col gap-1 rounded-md border border-border bg-surface-1/60 p-2">
                  {(history.data ?? []).slice(0, 10).map((h) => (
                    <li
                      key={h.run_id}
                      className="flex items-center gap-2 text-[12px] text-muted-foreground"
                    >
                      <RunStatusDot status={h.status} />
                      <span className="font-mono text-[11px] text-muted-foreground/80">
                        {h.human_key}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-foreground">
                        {h.agent_id}
                      </span>
                      <span className="shrink-0">
                        {h.created_at
                          ? new Date(h.created_at).toLocaleString([], {
                              month: "short",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })
                          : "—"}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </div>
        )}

        <DialogFooter>
          <Button
            variant="secondary"
            onClick={onClose}
            disabled={update.isPending}
          >
            Cancel
          </Button>
          <Button onClick={save} disabled={update.isPending || query.isLoading}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RunStatusDot({ status }: { status: string }) {
  const tone =
    status === "done"
      ? "bg-emerald-500"
      : status === "error"
        ? "bg-rose-500"
        : status === "running" || status === "queued"
          ? "bg-sky-500"
          : "bg-muted-foreground/40";
  return (
    <span
      title={`run ${status}`}
      className={`h-2 w-2 shrink-0 rounded-full ${tone}`}
    />
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-[11px] font-semibold uppercase tracking-[0.04em] text-muted-foreground/80">
        {title}
      </span>
      {children}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
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
