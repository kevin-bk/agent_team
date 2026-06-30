import { AlertTriangle, RefreshCw } from "@/components/icons";
import { useBoardFrictions } from "@/api/hooks";
import type { BoardFrictionDTO } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

interface FrictionPanelProps {
  boardId: string;
  /** Open a task's cockpit by its human key (e.g. "ABC-12"). */
  onOpenTask: (taskKey: string) => void;
}

/**
 * Board-level Friction page: a read-only list of "this was harder than it
 * should have been" signals across every task. Agents log them as they work and
 * the loop auto-emits one when a task is capped/budget-blocked. A human reviews
 * and acts on them manually — there is no automatic grouping or card creation.
 */
export function FrictionPanel({ boardId, onOpenTask }: FrictionPanelProps) {
  const frictions = useBoardFrictions(boardId);
  const rows = frictions.data ?? [];

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-background px-8 pb-6 pt-4">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-[15px] font-semibold text-foreground">Friction</h2>
        <span className="text-[13px] text-muted-foreground">
          {rows.length > 0 ? `${rows.length} signal${rows.length === 1 ? "" : "s"}` : ""}
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          aria-label="Refresh friction"
          onClick={() => frictions.refetch()}
          disabled={frictions.isFetching}
        >
          <RefreshCw className={cn("h-4 w-4", frictions.isFetching && "animate-spin")} />
          Refresh
        </Button>
      </div>

      <p className="mb-4 max-w-3xl text-[13px] leading-relaxed text-muted-foreground">
        Signals that work was harder than it should have been — missing tests,
        stale docs, ambiguous scope, a repeated manual step, or a task the loop
        couldn't verify. Review these and turn the recurring ones into a fix.
      </p>

      <div className="flex-1 overflow-y-auto">
        {frictions.isLoading ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full max-w-3xl" />
            ))}
          </div>
        ) : frictions.isError ? (
          <div className="text-sm text-muted-foreground">
            Couldn't load friction for this board.
          </div>
        ) : rows.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className="flex max-w-3xl flex-col gap-3">
            {rows.map((f) => (
              <FrictionCard key={f.id} friction={f} onOpenTask={onOpenTask} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

const SEVERITY_DOT: Record<BoardFrictionDTO["severity"], string> = {
  info: "bg-sky-500",
  warning: "bg-amber-500",
  blocking: "bg-red-500",
};

function FrictionCard({
  friction,
  onOpenTask,
}: {
  friction: BoardFrictionDTO;
  onOpenTask: (taskKey: string) => void;
}) {
  const when = friction.created_at ? Date.parse(friction.created_at) : null;
  return (
    <li className="rounded-lg border border-border bg-card p-3.5">
      <div className="flex items-start gap-2.5">
        <span
          className={cn(
            "mt-1.5 h-2 w-2 shrink-0 rounded-full",
            SEVERITY_DOT[friction.severity] ?? "bg-muted-foreground",
          )}
          title={friction.severity}
        />
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium text-foreground">
            {friction.title}
          </p>
          {friction.body && (
            <p className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed text-muted-foreground">
              {friction.body}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-muted-foreground">
            <button
              type="button"
              onClick={() => onOpenTask(friction.task_key)}
              className="inline-flex items-center gap-1 rounded bg-surface-1 px-1.5 py-0.5 font-medium text-primary transition-colors hover:bg-primary/10"
              title={friction.task_title}
            >
              {friction.task_key}
            </button>
            <span className="truncate">{friction.task_title}</span>
            <span aria-hidden>·</span>
            <span className="capitalize">{friction.actor_type}</span>
            {when && (
              <>
                <span aria-hidden>·</span>
                <span>{relativeTime(when)}</span>
              </>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border py-12 text-center">
      <AlertTriangle className="h-6 w-6 text-muted-foreground" />
      <p className="text-sm font-medium text-foreground">No friction yet</p>
      <p className="max-w-sm text-[13px] text-muted-foreground">
        As agents work, anything that slowed them down shows up here. Nothing to
        review right now.
      </p>
    </div>
  );
}
