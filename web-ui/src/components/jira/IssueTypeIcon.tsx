import {
  AlertTriangle,
  Bot,
  Check,
  ListChecks,
  Sparkles,
} from "@/components/icons";
import type { TaskType } from "@/api/types";
import { cn } from "@/lib/utils";

/**
 * Jira issue-type glyph (coloured square with a white icon). The type is now a
 * real, editable field on the task ({@link TaskType}); {@link issueTypeFromTask}
 * stays as a labels-based fallback for rows created before the field existed.
 */
export type IssueType = TaskType;

export const ISSUE_TYPE_META: Record<
  IssueType,
  { bg: string; Icon: (p: { className?: string }) => JSX.Element; label: string }
> = {
  task: { bg: "bg-[#4BADE8]", Icon: Check, label: "Task" },
  story: { bg: "bg-[#65BA43]", Icon: ListChecks, label: "Story" },
  bug: { bg: "bg-[#E5493A]", Icon: AlertTriangle, label: "Bug" },
  epic: { bg: "bg-[#8E44AD]", Icon: Sparkles, label: "Epic" },
  subtask: { bg: "bg-[#4BADE8]", Icon: Check, label: "Subtask" },
  agent: { bg: "bg-[#5243AA]", Icon: Bot, label: "Agent task" },
};

const META = ISSUE_TYPE_META;

/** Selectable types in the order they appear in dropdowns. */
export const ISSUE_TYPE_ORDER: IssueType[] = [
  "task",
  "story",
  "bug",
  "epic",
  "subtask",
  "agent",
];

export function issueTypeFromTask(task: { labels?: string[] }): IssueType {
  const labels = (task.labels ?? []).map((l) => l.toLowerCase());
  if (labels.some((l) => /bug|defect|incident/.test(l))) return "bug";
  if (labels.some((l) => /epic/.test(l))) return "epic";
  if (labels.some((l) => /story|feature/.test(l))) return "story";
  if (labels.some((l) => /sub-?task/.test(l))) return "subtask";
  if (labels.some((l) => /agent|ai|bot/.test(l))) return "agent";
  return "task";
}

/**
 * The task's issue type: the persisted {@link TaskType} when set, otherwise the
 * labels-based guess so older tasks still render a sensible glyph.
 */
export function taskIssueType(task: {
  task_type?: TaskType | null;
  labels?: string[];
}): IssueType {
  return task.task_type && task.task_type in META
    ? task.task_type
    : issueTypeFromTask(task);
}

export function IssueTypeIcon({
  type = "task",
  size = 16,
  className,
}: {
  type?: IssueType;
  size?: number;
  className?: string;
}) {
  const meta = META[type] ?? META.task;
  const { bg, Icon, label } = meta;
  return (
    <span
      title={label}
      aria-label={label}
      style={{ width: size, height: size }}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-[3px] text-white",
        bg,
        className,
      )}
    >
      <Icon className="h-[70%] w-[70%]" />
    </span>
  );
}
