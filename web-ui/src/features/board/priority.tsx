import {
  PriorityHigh,
  PriorityHighest,
  PriorityLow,
  PriorityLowest,
  PriorityMedium,
} from "@/components/icons";
import type { TaskPriority } from "@/api/types";
import { cn } from "@/lib/utils";

interface PriorityMeta {
  label: string;
  /** Tailwind text-color class (Jira-style: red→amber→blue). */
  color: string;
  Icon: (props: { className?: string }) => JSX.Element;
}

/** Jira priority scale, highest → lowest, with ADS palette colors. */
export const PRIORITY_META: Record<TaskPriority, PriorityMeta> = {
  highest: { label: "Highest", color: "text-red-600", Icon: PriorityHighest },
  high: { label: "High", color: "text-red-500", Icon: PriorityHigh },
  medium: { label: "Medium", color: "text-yellow-500", Icon: PriorityMedium },
  low: { label: "Low", color: "text-blue-500", Icon: PriorityLow },
  lowest: { label: "Lowest", color: "text-blue-400", Icon: PriorityLowest },
};

export const PRIORITY_ORDER: TaskPriority[] = [
  "highest",
  "high",
  "medium",
  "low",
  "lowest",
];

/** Colored priority glyph (Jira-style). Returns null when no priority set. */
export function PriorityIcon({
  priority,
  className,
}: {
  priority: TaskPriority | null | undefined;
  className?: string;
}) {
  if (!priority) return null;
  const meta = PRIORITY_META[priority];
  if (!meta) return null;
  const { Icon, color, label } = meta;
  return (
    <span title={`${label} priority`} aria-label={`${label} priority`}>
      <Icon className={cn(color, "h-4 w-4", className)} />
    </span>
  );
}
