import { useState } from "react";
import { toast } from "sonner";
import { Cpu, Loader2, RefreshCw, Trash2 } from "@/components/icons";
import {
  useAdminSandboxAction,
  useAdminSandboxes,
  useMe,
} from "@/api/hooks";
import type { SandboxAdminRow } from "@/api/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

const STATE_BADGE: Record<
  string,
  { label: string; variant: "default" | "success" | "destructive" | "outline" }
> = {
  running: { label: "Running", variant: "success" },
  paused: { label: "Paused", variant: "default" },
};

const SOURCE_BADGE: Record<
  string,
  { label: string; variant: "default" | "success" | "destructive" | "outline"; hint: string }
> = {
  tracked: {
    label: "Tracked",
    variant: "success",
    hint: "Actively managed by this app process",
  },
  persisted: {
    label: "Persisted",
    variant: "default",
    hint: "Linked to a task in the DB; will be reattached on its next run",
  },
  orphan: {
    label: "Orphan",
    variant: "destructive",
    hint: "Only the OpenSandbox server knows this sandbox — safe to kill",
  },
  stale_link: {
    label: "Stale link",
    variant: "outline",
    hint: "A task still points at this id but the server no longer has it",
  },
};

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const m = Math.round((Date.now() - then) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function inTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const m = Math.round((then - Date.now()) / 60000);
  if (m <= 0) return "expired";
  if (m < 60) return `in ${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `in ${h}h`;
  return `in ${Math.round(h / 24)}d`;
}

function idleLabel(seconds: number | undefined): string {
  if (seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  return `${Math.round(m / 60)}h`;
}

function metricsLabel(row: SandboxAdminRow): string {
  const m = row.metrics;
  if (!m) return "—";
  const mem = `${Math.round(m.memory_used_mib)}/${Math.round(m.memory_total_mib)} MiB`;
  return `${m.cpu_used_percentage.toFixed(0)}% CPU · ${mem}`;
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border bg-card px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-0.5 text-xl font-semibold ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

/** Admin-only overview of every sandbox: manage (pause/kill) + quick analytics. */
export function SandboxesPage() {
  const me = useMe();
  const isAdmin = !!me.data?.is_admin;
  const overview = useAdminSandboxes(isAdmin);
  const action = useAdminSandboxAction();
  const confirm = useConfirm();
  const [busyId, setBusyId] = useState<string | null>(null);

  if (me.data && !isAdmin) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Sandbox management is available to administrators only.
      </div>
    );
  }

  const data = overview.data;

  const run = async (row: SandboxAdminRow, act: "pause" | "kill") => {
    if (act === "kill") {
      const ok = await confirm({
        title: "Kill sandbox?",
        description:
          `Sandbox ${shortId(row.sandbox_id)}` +
          (row.task_key ? ` (task ${row.task_key})` : "") +
          " will be destroyed. Installed packages and shell state are lost; the" +
          " task's next run reprovisions a fresh sandbox.",
        confirmLabel: "Kill",
        tone: "danger",
      });
      if (!ok) return;
    }
    setBusyId(row.sandbox_id);
    try {
      await action.mutateAsync({ sandboxId: row.sandbox_id, action: act });
      toast.success(act === "kill" ? "Sandbox killed" : "Sandbox paused");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `Failed to ${act} sandbox`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-6 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-semibold">
              <Cpu className="h-5 w-5" /> Sandboxes
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Every isolated runtime the OpenSandbox server holds — tracked tasks,
              reattachable ones, and orphans.
              {data?.server_url ? ` Server: ${data.server_url}` : ""}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void overview.refetch()}
            disabled={overview.isFetching}
          >
            {overview.isFetching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            Refresh
          </Button>
        </div>

        {data?.server_error ? (
          <div className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-2 text-sm text-destructive">
            Could not reach the OpenSandbox server: {data.server_error}. Showing
            only what this app process tracks.
          </div>
        ) : null}

        {overview.isLoading ? (
          <div className="flex h-48 items-center justify-center">
            <Spinner />
          </div>
        ) : data ? (
          <>
            <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <StatCard label="Total" value={String(data.total)} />
              <StatCard
                label="Running"
                value={String(data.counts.running)}
                tone="text-emerald-600 dark:text-emerald-400"
              />
              <StatCard label="Paused" value={String(data.counts.paused)} />
              <StatCard
                label="Orphans"
                value={String(data.counts.orphan)}
                tone={data.counts.orphan > 0 ? "text-destructive" : undefined}
              />
              <StatCard
                label="Capacity"
                value={
                  data.max_concurrent > 0
                    ? `${data.tracked}/${data.max_concurrent}`
                    : `${data.tracked}/∞`
                }
              />
              <StatCard
                label="Idle TTL"
                value={
                  data.idle_ttl_seconds > 0
                    ? `${Math.round(data.idle_ttl_seconds / 60)}m`
                    : "off"
                }
              />
            </div>

            <div className="mt-5 overflow-x-auto rounded-lg border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-muted/50 text-left text-xs text-muted-foreground">
                    <th className="px-3 py-2 font-medium">Sandbox</th>
                    <th className="px-3 py-2 font-medium">Task</th>
                    <th className="px-3 py-2 font-medium">Board</th>
                    <th className="px-3 py-2 font-medium">State</th>
                    <th className="px-3 py-2 font-medium">Source</th>
                    <th className="px-3 py-2 font-medium">Usage</th>
                    <th className="px-3 py-2 font-medium">Idle</th>
                    <th className="px-3 py-2 font-medium">Created</th>
                    <th className="px-3 py-2 font-medium">Expires</th>
                    <th className="px-3 py-2 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {data.sandboxes.length === 0 ? (
                    <tr>
                      <td
                        colSpan={10}
                        className="px-3 py-8 text-center text-muted-foreground"
                      >
                        No sandboxes right now — they appear when a task on an
                        isolated board runs.
                      </td>
                    </tr>
                  ) : (
                    data.sandboxes.map((row) => {
                      const state = row.ui_state
                        ? STATE_BADGE[row.ui_state]
                        : undefined;
                      const source = SOURCE_BADGE[row.source];
                      const busy = busyId === row.sandbox_id;
                      const canPause = row.ui_state === "running";
                      const dead = row.source === "stale_link";
                      return (
                        <tr key={row.sandbox_id} className="border-b last:border-0">
                          <td className="px-3 py-2 font-mono text-xs" title={row.sandbox_id}>
                            {shortId(row.sandbox_id)}
                          </td>
                          <td className="px-3 py-2">
                            {row.task_key ? (
                              <span title={row.task_title ?? undefined}>
                                {row.task_key}
                              </span>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            {row.board_name ?? (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            {state ? (
                              <Badge variant={state.variant}>{state.label}</Badge>
                            ) : (
                              <Badge variant="outline">
                                {row.server_state ?? "unknown"}
                              </Badge>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            {source ? (
                              <Badge variant={source.variant} title={source.hint}>
                                {source.label}
                              </Badge>
                            ) : null}
                          </td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            {metricsLabel(row)}
                          </td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            {idleLabel(row.idle_seconds)}
                          </td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            {relTime(row.created_at)}
                          </td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            {inTime(row.expires_at)}
                          </td>
                          <td className="px-3 py-2">
                            {dead ? null : (
                              <div className="flex justify-end gap-1.5">
                                {canPause ? (
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={busy}
                                    onClick={() => void run(row, "pause")}
                                  >
                                    Pause
                                  </Button>
                                ) : null}
                                <Button
                                  variant="destructive"
                                  size="sm"
                                  disabled={busy}
                                  onClick={() => void run(row, "kill")}
                                >
                                  {busy ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                  ) : (
                                    <Trash2 className="h-3.5 w-3.5" />
                                  )}
                                  Kill
                                </Button>
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
