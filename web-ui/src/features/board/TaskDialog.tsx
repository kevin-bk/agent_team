import { Trash2 } from "@/components/icons";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useArchiveTask,
  useBoardMembers,
  useCreateTask,
  usePatchTask,
} from "@/api/hooks";
import type {
  BoardColumn,
  TaskDTO,
  TaskPriority,
  TaskType,
} from "@/api/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { UserSelect } from "@/components/UserSelect";
import {
  IssueTypeIcon,
  ISSUE_TYPE_META,
  ISSUE_TYPE_ORDER,
} from "@/components/jira";
import { cn } from "@/lib/utils";
import { PRIORITY_META, PRIORITY_ORDER, PriorityIcon } from "./priority";
import { statusColor } from "./statusColor";
import { Button } from "@/components/ui/button";
import { SelectMenu } from "@/components/ui/select-menu";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NoteEditor } from "./cockpit/NoteEditor";

interface FormState {
  title: string;
  description: string;
  task_type: TaskType;
  status: string;
  priority: string;
  labels: string;
  assignee_id: string;
  jira_key: string;
  jira_url: string;
}

function toForm(task: TaskDTO | null, fallbackStatus: string): FormState {
  return {
    title: task?.title ?? "",
    description: task?.description ?? "",
    task_type: task?.task_type ?? "task",
    status: task?.status ?? fallbackStatus,
    priority: task?.priority ?? "",
    labels: (task?.labels ?? []).join(", "),
    assignee_id: task?.assignee_id ?? "",
    jira_key: task?.jira_key ?? "",
    jira_url: task?.jira_url ?? "",
  };
}

function parseLabels(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function TaskDialog({
  boardId,
  columns,
  task,
  defaultStatus,
  open,
  onClose,
}: {
  boardId: string;
  columns: BoardColumn[];
  task: TaskDTO | null;
  defaultStatus: string;
  open: boolean;
  onClose: () => void;
}) {
  const isEdit = task !== null;
  const [form, setForm] = useState<FormState>(() => toForm(task, defaultStatus));
  const confirm = useConfirm();

  const create = useCreateTask(boardId);
  const patch = usePatchTask(boardId);
  const archive = useArchiveTask(boardId);
  const members = useBoardMembers(open ? boardId : undefined);
  const memberOptions = useMemo(
    () =>
      (members.data ?? []).map((m) => ({
        id: m.user_id,
        name: m.display_name || m.email || m.user_id,
        email: m.email,
        avatar: m.avatar_url,
      })),
    [members.data],
  );

  // Re-seed the form whenever the dialog opens for a different task/column.
  useEffect(() => {
    if (open) setForm(toForm(task, defaultStatus));
  }, [open, task, defaultStatus]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const busy = create.isPending || patch.isPending || archive.isPending;

  const submit = async () => {
    const title = form.title.trim();
    if (!title) {
      toast.error("Title is required");
      return;
    }
    const labels = parseLabels(form.labels);
    const priority = (form.priority || null) as TaskPriority | null;
    try {
      if (isEdit && task) {
        await patch.mutateAsync({
          taskId: task.id,
          body: {
            title,
            description: form.description.trim() || null,
            task_type: form.task_type,
            status: form.status,
            assignee_id: form.assignee_id.trim() || null,
            labels,
            priority,
            jira_key: form.jira_key.trim() || null,
            jira_url: form.jira_url.trim() || null,
          },
        });
        toast.success(`Updated ${task.human_key}`);
      } else {
        const created = await create.mutateAsync({
          board_id: boardId,
          title,
          task_type: form.task_type,
          status: form.status,
          description: form.description.trim() || null,
          assignee_id: form.assignee_id.trim() || null,
          labels,
          priority,
          jira_key: form.jira_key.trim() || null,
          jira_url: form.jira_url.trim() || null,
        });
        toast.success(`Created ${created.human_key}`);
      }
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save task");
    }
  };

  const onArchive = async () => {
    if (!task) return;
    const ok = await confirm({
      title: `Archive ${task.human_key}?`,
      description: "It will be hidden from the board. This can't be undone here.",
      tone: "danger",
      confirmLabel: "Archive",
    });
    if (!ok) return;
    try {
      await archive.mutateAsync(task.id);
      toast.success(`Archived ${task.human_key}`);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to archive");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-h-[88vh] w-[92vw] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? `Edit ${task?.human_key}` : "Create task"}
          </DialogTitle>
        </DialogHeader>

        <div className="grid gap-4 pt-1">
          <Field
            label="Short summary"
            hint="Concisely summarize the task in one or two sentences."
          >
            <Input
              value={form.title}
              autoFocus
              placeholder="What needs to be done?"
              onChange={(e) => set("title", e.target.value)}
            />
          </Field>

          <div className="grid gap-1.5">
            <span className="text-[13px] font-medium text-muted-foreground">
              Description
            </span>
            <NoteEditor
              value={form.description}
              onChange={(md) => set("description", md)}
              onSubmit={submit}
              placeholder="Describe the task in as much detail as you'd like"
              disabled={busy}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Status">
              <SelectMenu
                value={form.status}
                onChange={(v) => set("status", v)}
                options={columns.map((c) => ({
                  value: c.key,
                  label: c.name,
                  icon: (
                    <span
                      className={cn(
                        "h-2.5 w-2.5 rounded-sm",
                        statusColor(c.key, c.name).dot,
                      )}
                    />
                  ),
                }))}
              />
            </Field>
            <Field label="Priority">
              <SelectMenu
                value={form.priority}
                onChange={(v) => set("priority", v)}
                placeholder="None"
                options={[
                  {
                    value: "",
                    label: "None",
                    icon: <PriorityIcon priority={null} />,
                  },
                  ...PRIORITY_ORDER.map((p) => ({
                    value: p,
                    label: PRIORITY_META[p].label,
                    icon: <PriorityIcon priority={p} />,
                  })),
                ]}
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Type">
              <SelectMenu
                value={form.task_type}
                onChange={(v) => set("task_type", (v || "task") as TaskType)}
                options={ISSUE_TYPE_ORDER.map((t) => ({
                  value: t,
                  label: ISSUE_TYPE_META[t].label,
                  icon: <IssueTypeIcon type={t} size={16} />,
                }))}
              />
            </Field>
            <Field label="Assignee">
              <UserSelect
                options={memberOptions}
                value={form.assignee_id || null}
                onChange={(id) => set("assignee_id", id ?? "")}
                placeholder="Unassigned"
                allowUnassigned
                loading={members.isLoading}
              />
            </Field>
          </div>

          <Field label="Labels" hint="Separate multiple labels with commas.">
            <Input
              value={form.labels}
              placeholder="frontend, urgent"
              onChange={(e) => set("labels", e.target.value)}
            />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Jira key">
              <Input
                value={form.jira_key}
                placeholder="CHZ-123"
                onChange={(e) => set("jira_key", e.target.value)}
              />
            </Field>
            <Field label="Jira URL">
              <Input
                value={form.jira_url}
                placeholder="https://…"
                onChange={(e) => set("jira_url", e.target.value)}
              />
            </Field>
          </div>
        </div>

        <DialogFooter className="items-center">
          {isEdit && (
            <Button
              variant="ghost"
              className="mr-auto text-destructive hover:text-destructive"
              onClick={onArchive}
              disabled={busy}
            >
              <Trash2 className="h-4 w-4" /> Archive
            </Button>
          )}
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy}>
            {isEdit ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  /** Jira-style helper line shown under the control. */
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-1.5">
      <span className="text-[13px] font-medium text-muted-foreground">
        {label}
      </span>
      {children}
      {hint && (
        <span className="text-[12.5px] font-normal text-muted-foreground/80">
          {hint}
        </span>
      )}
    </label>
  );
}
