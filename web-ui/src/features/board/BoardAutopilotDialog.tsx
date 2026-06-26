import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useAgents,
  useAutopilot,
  useAutopilotSummary,
  useCliTargets,
  useRouteAutopilot,
  useUpdateAutopilot,
} from "@/api/hooks";
import type {
  AutopilotScheduleMode,
  BoardDTO,
  PatchAutopilotBody,
  RoutingRule,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import { PRIORITY_META, PRIORITY_ORDER } from "./priority";
import { cn } from "@/lib/utils";
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
/**
 * Configure a board's autopilot: on a schedule, the autopilot scans the source
 * column for tasks that carry an agent assignee and runs them, moving each
 * through the working → done/error columns. Only agent-assigned tasks are
 * picked, and each runs as its assigned agent.
 */
export function BoardAutopilotDialog({
  board,
  open,
  onClose,
}: {
  board: BoardDTO;
  open: boolean;
  onClose: () => void;
}) {
  const query = useAutopilot(open ? board.id : undefined);
  const summary = useAutopilotSummary(open ? board.id : undefined);
  const update = useUpdateAutopilot(board.id);
  const route = useRouteAutopilot(board.id);
  const agents = useAgents();
  const cliTargets = useCliTargets();
  const columns = board.columns ?? [];
  const colName = (key: string) =>
    columns.find((c) => c.key === key)?.name ?? key;

  const [form, setForm] = useState<PatchAutopilotBody>({});

  // Agents/CLIs staffed on this board — the only ones autopilot can run, and
  // therefore the only ones worth a per-agent concurrency override.
  const staffedAgents = useMemo(() => {
    const out: { id: string; label: string }[] = [];
    const enabledAgents = new Set(board.agent_ids ?? []);
    const enabledClis = new Set(board.cli_target_ids ?? []);
    for (const a of agents.data ?? [])
      if (enabledAgents.has(a.id)) out.push({ id: a.id, label: a.display_name });
    for (const t of cliTargets.data ?? [])
      if (enabledClis.has(t.id)) out.push({ id: t.id, label: t.label });
    return out;
  }, [board.agent_ids, board.cli_target_ids, agents.data, cliTargets.data]);

  // Seed the form from the loaded config whenever it (re)opens.
  useEffect(() => {
    if (open && query.data) {
      const d = query.data;
      setForm({
        enabled: d.enabled,
        schedule_mode: d.schedule_mode,
        interval_seconds: d.interval_seconds,
        cron: d.cron ?? "",
        timezone: d.timezone,
        source_status: d.source_status,
        working_status: d.working_status,
        done_status: d.done_status,
        error_status: d.error_status,
        board_concurrency: d.board_concurrency,
        default_agent_concurrency: d.default_agent_concurrency,
        agent_concurrency: { ...(d.agent_concurrency ?? {}) },
        error_cooldown_seconds: d.error_cooldown_seconds,
        max_attempts: d.max_attempts,
        routing_rules: (d.routing_rules ?? []).map((r) => ({ ...r })),
      });
    }
  }, [open, query.data]);

  const set = <K extends keyof PatchAutopilotBody>(
    key: K,
    value: PatchAutopilotBody[K],
  ) => setForm((f) => ({ ...f, [key]: value }));

  // Set/clear a single agent's override. An empty value removes the key so the
  // agent falls back to the board default.
  const setAgentCap = (agentId: string, raw: string) =>
    setForm((f) => {
      const next = { ...(f.agent_concurrency ?? {}) };
      const trimmed = raw.trim();
      if (trimmed === "") delete next[agentId];
      else next[agentId] = Math.max(1, Number(trimmed) || 1);
      return { ...f, agent_concurrency: next };
    });

  const mode = (form.schedule_mode ?? "off") as AutopilotScheduleMode;
  const rules = form.routing_rules ?? [];

  const setRules = (next: RoutingRule[]) => set("routing_rules", next);
  const updateRule = (i: number, patch: Partial<RoutingRule>) =>
    setRules(rules.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRule = () =>
    setRules([...rules, { labels: [], priorities: [], agents: [] }]);
  const removeRule = (i: number) =>
    setRules(rules.filter((_, idx) => idx !== i));
  const toggle = (list: string[], value: string) =>
    list.includes(value)
      ? list.filter((x) => x !== value)
      : [...list, value];

  const save = async () => {
    try {
      await update.mutateAsync(form);
      toast.success("Autopilot updated");
      onClose();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to update autopilot",
      );
    }
  };

  // Persist the current rules first (so the run uses what's on screen), then
  // apply routing once across the board's unassigned source-column tasks.
  const autoAssign = async () => {
    try {
      await update.mutateAsync(form);
      const res = await route.mutateAsync();
      toast.success(
        res.assigned > 0
          ? `Assigned ${res.assigned} task${res.assigned === 1 ? "" : "s"}`
          : "No unassigned tasks matched a rule",
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to auto-assign");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="w-[92vw] max-w-3xl">
        <DialogHeader>
          <DialogTitle>Autopilot</DialogTitle>
          <DialogDescription>
            On a schedule, automatically pick up tasks that are assigned to an
            agent and run them — moving each through the columns below. Only
            agent-assigned tasks in the source column are picked.
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
                Enable autopilot for this board
              </span>
              <Switch
                checked={!!form.enabled}
                onCheckedChange={(v) => set("enabled", v)}
              />
            </label>

            {/* Live status panel */}
            {summary.data && (
              <div className="rounded-md border border-border bg-surface-1/60 px-3 py-2.5">
                <div className="grid grid-cols-3 gap-2 text-center">
                  <Stat
                    label="In flight"
                    value={`${summary.data.in_flight}/${summary.data.board_concurrency}`}
                  />
                  <Stat label="Runs today" value={String(summary.data.runs_today)} />
                  <Stat
                    label="Next scan"
                    value={
                      summary.data.enabled && summary.data.next_run_at
                        ? new Date(summary.data.next_run_at).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })
                        : "—"
                    }
                  />
                </div>
                {summary.data.recent.length > 0 && (
                  <ul className="mt-2.5 flex flex-col gap-1 border-t border-border pt-2">
                    {summary.data.recent.slice(0, 4).map((r) => (
                      <li
                        key={`${r.task_id}-${r.at ?? ""}`}
                        className="flex items-center gap-2 text-[12px] text-muted-foreground"
                      >
                        <span className="font-mono text-[11px] text-muted-foreground/80">
                          {r.human_key}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-foreground">
                          {r.title}
                        </span>
                        <RunStatusDot status={r.run_status} />
                        <span className="shrink-0">{colName(r.status)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
              {/* Left column: schedule */}
              <Section title="Schedule">
                <div className="flex gap-2 text-sm">
                  {(["off", "interval", "cron"] as const).map((m) => (
                    <TabButton
                      key={m}
                      active={mode === m}
                      onClick={() => set("schedule_mode", m)}
                    >
                      {m === "off" ? "Off" : m === "interval" ? "Interval" : "Cron"}
                    </TabButton>
                  ))}
                </div>
                {mode === "interval" && (
                  <Field label="Run every (minutes)">
                    <Input
                      type="number"
                      min={1}
                      value={Math.max(
                        1,
                        Math.round((form.interval_seconds ?? 3600) / 60),
                      )}
                      onChange={(e) =>
                        set(
                          "interval_seconds",
                          Math.max(60, Number(e.target.value || 1) * 60),
                        )
                      }
                    />
                  </Field>
                )}
                {mode === "cron" && (
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
                )}
                {mode === "off" && (
                  <span className="text-[12px] text-muted-foreground">
                    No schedule — the board won't auto-run until you pick Interval
                    or Cron.
                  </span>
                )}
              </Section>

              {/* Right column: status mapping */}
              <Section title="Status mapping">
                <div className="grid grid-cols-2 gap-3">
                  <ColumnSelect
                    label="Source"
                    value={form.source_status}
                    columns={columns}
                    onChange={(v) => set("source_status", v)}
                  />
                  <ColumnSelect
                    label="Working"
                    value={form.working_status}
                    columns={columns}
                    onChange={(v) => set("working_status", v)}
                  />
                  <ColumnSelect
                    label="Done"
                    value={form.done_status}
                    columns={columns}
                    onChange={(v) => set("done_status", v)}
                  />
                  <ColumnSelect
                    label="On error"
                    value={form.error_status}
                    columns={columns}
                    onChange={(v) => set("error_status", v)}
                  />
                </div>
              </Section>

              {/* Concurrency */}
              <Section title="Concurrency">
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Max runs (whole board)">
                    <Input
                      type="number"
                      min={1}
                      value={form.board_concurrency ?? 2}
                      onChange={(e) =>
                        set(
                          "board_concurrency",
                          Math.max(1, Number(e.target.value || 1)),
                        )
                      }
                    />
                  </Field>
                  <Field label="Default per agent">
                    <Input
                      type="number"
                      min={1}
                      value={form.default_agent_concurrency ?? 1}
                      onChange={(e) =>
                        set(
                          "default_agent_concurrency",
                          Math.max(1, Number(e.target.value || 1)),
                        )
                      }
                    />
                  </Field>
                </div>
              </Section>

              {/* Failure handling */}
              <Section title="Failure handling">
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Retry cooldown (minutes)">
                    <Input
                      type="number"
                      min={0}
                      value={Math.round((form.error_cooldown_seconds ?? 3600) / 60)}
                      onChange={(e) =>
                        set(
                          "error_cooldown_seconds",
                          Math.max(0, Number(e.target.value || 0) * 60),
                        )
                      }
                    />
                  </Field>
                  <Field label="Max attempts">
                    <Input
                      type="number"
                      min={1}
                      value={form.max_attempts ?? 3}
                      onChange={(e) =>
                        set("max_attempts", Math.max(1, Number(e.target.value || 1)))
                      }
                    />
                  </Field>
                </div>
              </Section>
            </div>

            {/* Per-agent concurrency overrides */}
            <Section title="Per-agent limits (optional)">
              {staffedAgents.length === 0 ? (
                <span className="text-[12px] text-muted-foreground">
                  Staff agents on this board to set per-agent run limits.
                </span>
              ) : (
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {staffedAgents.map((a) => (
                    <div
                      key={a.id}
                      className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-1.5"
                    >
                      <span className="min-w-0 truncate text-[13px] text-foreground">
                        {a.label}
                      </span>
                      <Input
                        type="number"
                        min={1}
                        className="h-8 w-20"
                        placeholder={String(form.default_agent_concurrency ?? 1)}
                        value={form.agent_concurrency?.[a.id] ?? ""}
                        onChange={(e) => setAgentCap(a.id, e.target.value)}
                      />
                    </div>
                  ))}
                </div>
              )}
            </Section>

            {/* Routing rules — manual "Auto-assign" only */}
            <Section title="Routing rules (auto-assign agents)">
              <p className="-mt-1 text-[12px] text-muted-foreground">
                Match unassigned tasks in the source column to a group of agents.
                First matching rule wins; its agents are used round-robin. Runs
                only when you click "Auto-assign" — never on the schedule.
              </p>
              {staffedAgents.length === 0 ? (
                <span className="text-[12px] text-muted-foreground">
                  Staff agents on this board to build routing rules.
                </span>
              ) : (
                <div className="flex flex-col gap-2">
                  {rules.map((rule, i) => (
                    <div
                      key={i}
                      className="flex flex-col gap-2 rounded-md border border-border p-2.5"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[12px] font-medium text-muted-foreground">
                          Rule {i + 1}
                        </span>
                        <button
                          type="button"
                          onClick={() => removeRule(i)}
                          className="text-[12px] text-destructive hover:underline"
                        >
                          Remove
                        </button>
                      </div>
                      <Field label="Labels (any of, comma-separated — empty = any)">
                        <Input
                          value={rule.labels.join(", ")}
                          placeholder="frontend, urgent"
                          onChange={(e) =>
                            updateRule(i, {
                              labels: e.target.value
                                .split(",")
                                .map((s) => s.trim())
                                .filter(Boolean),
                            })
                          }
                        />
                      </Field>
                      <div className="flex flex-col gap-1.5">
                        <span className="text-xs font-medium text-muted-foreground">
                          Priorities (empty = any)
                        </span>
                        <div className="flex flex-wrap gap-1.5">
                          {PRIORITY_ORDER.map((p) => (
                            <Chip
                              key={p}
                              active={rule.priorities.includes(p)}
                              onClick={() =>
                                updateRule(i, {
                                  priorities: toggle(rule.priorities, p),
                                })
                              }
                            >
                              {PRIORITY_META[p].label}
                            </Chip>
                          ))}
                        </div>
                      </div>
                      <div className="flex flex-col gap-1.5">
                        <span className="text-xs font-medium text-muted-foreground">
                          Agents (round-robin within this rule)
                        </span>
                        <div className="flex flex-wrap gap-1.5">
                          {staffedAgents.map((a) => (
                            <Chip
                              key={a.id}
                              active={rule.agents.includes(a.id)}
                              onClick={() =>
                                updateRule(i, {
                                  agents: toggle(rule.agents, a.id),
                                })
                              }
                            >
                              {a.label}
                            </Chip>
                          ))}
                        </div>
                      </div>
                    </div>
                  ))}
                  <div className="flex items-center gap-2">
                    <Button variant="secondary" onClick={addRule}>
                      Add rule
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={autoAssign}
                      disabled={
                        rules.length === 0 || update.isPending || route.isPending
                      }
                    >
                      Auto-assign now
                    </Button>
                  </div>
                </div>
              )}
            </Section>

            <p className="text-[12px] text-muted-foreground">
              Auto-runs are seeded with the board's{" "}
              <span className="font-medium text-foreground">Starter prompt</span>{" "}
              (Board settings). Leave it empty to use the default (work on the
              task; read <code>.agent-team/TASK.md</code>).
            </p>

            {query.data?.next_run_at && form.enabled && (
              <span className="text-[12px] text-muted-foreground">
                Next scan: {new Date(query.data.next_run_at).toLocaleString()}
              </span>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={update.isPending}>
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

function Chip({
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
      className={cn(
        "rounded-full border px-2.5 py-0.5 text-[12px] transition-colors",
        active
          ? "border-primary bg-primary/10 text-primary"
          : "border-border text-muted-foreground hover:bg-accent",
      )}
    >
      {children}
    </button>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[15px] font-semibold text-foreground">{value}</span>
      <span className="text-[11px] text-muted-foreground">{label}</span>
    </div>
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

function ColumnSelect({
  label,
  value,
  columns,
  onChange,
}: {
  label: string;
  value: string | undefined;
  columns: { key: string; name: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label}>
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 rounded-md border border-border bg-surface-1 px-2 text-[13px] text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
      >
        {columns.map((c) => (
          <option key={c.key} value={c.key}>
            {c.name}
          </option>
        ))}
      </select>
    </Field>
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
