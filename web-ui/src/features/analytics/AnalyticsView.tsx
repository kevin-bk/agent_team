import { Activity, Clock, Coins, Cpu, Gauge, Hash } from "@/components/icons";
import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useAgents, useBoards, useRunStats } from "@/api/hooks";
import type { RunStatsResponse } from "@/api/types";
import { PageHeader } from "@/components/jira";
import { Skeleton } from "@/components/ui/skeleton";
import { formatCost, formatDuration, formatTokens } from "@/lib/format";

const DAY_OPTIONS = [7, 30, 90];

const STATUS_COLORS: Record<string, string> = {
  done: "#22A06B",
  error: "#E2483D",
  running: "#388BFF",
  queued: "#E2B203",
  cancelled: "#8590A2",
};

export function AnalyticsView() {
  const boards = useBoards();
  const agents = useAgents();
  const [boardId, setBoardId] = useState<string>();
  const [days, setDays] = useState(30);
  const [agentId, setAgentId] = useState<string>("");

  useEffect(() => {
    if (!boardId && boards.data && boards.data.length > 0) {
      setBoardId(boards.data[0].id);
    }
  }, [boards.data, boardId]);

  const stats = useRunStats(boardId, days, agentId || undefined);

  return (
    <div className="flex h-full flex-col bg-background">
      <PageHeader
        breadcrumbs={[{ label: "Reports" }, { label: "Analytics" }]}
        title="Analytics"
        actions={
          <>
            <Select
              value={boardId ?? ""}
              onChange={setBoardId}
              ariaLabel="Board"
              options={(boards.data ?? []).map((b) => ({
                value: b.id,
                label: b.name,
              }))}
            />
            <Select
              value={agentId}
              onChange={setAgentId}
              ariaLabel="Agent"
              options={[
                { value: "", label: "All agents" },
                ...(agents.data ?? []).map((a) => ({
                  value: a.id,
                  label: a.display_name,
                })),
              ]}
            />
            <div className="flex items-center gap-0.5 rounded border border-border bg-surface-2 p-0.5">
              {DAY_OPTIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setDays(d)}
                  className={
                    days === d
                      ? "rounded bg-card px-2.5 py-1 text-xs font-medium text-foreground shadow-raised"
                      : "rounded px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
                  }
                >
                  {d}d
                </button>
              ))}
            </div>
          </>
        }
      />

      <div className="min-h-0 flex-1 overflow-auto p-6 scrollbar-thin">
        {boards.data && boards.data.length === 0 ? (
          <Empty text="No boards yet — create one to see analytics." />
        ) : stats.isLoading ? (
          <LoadingSkeleton />
        ) : stats.data ? (
          <Dashboard data={stats.data} />
        ) : (
          <Empty text="Couldn't load analytics for this board." />
        )}
      </div>
    </div>
  );
}

function Dashboard({ data }: { data: RunStatsResponse }) {
  if (data.total_runs === 0) {
    return <Empty text="No agent runs in this window yet." />;
  }
  const statusData = Object.entries(data.by_status).map(([name, value]) => ({
    name,
    value,
  }));
  const agentData = data.by_agent.slice(0, 8).map((a) => ({
    name: a.agent_id,
    runs: a.runs,
  }));

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <Kpi
          icon={<Activity className="h-4 w-4" />}
          label="Runs"
          value={String(data.total_runs)}
        />
        <Kpi
          icon={<Gauge className="h-4 w-4" />}
          label="Success rate"
          value={
            data.success_rate == null
              ? "—"
              : `${Math.round(data.success_rate * 100)}%`
          }
        />
        <Kpi
          icon={<Coins className="h-4 w-4" />}
          label="Cost"
          value={formatCost(data.total_cost_usd)}
        />
        <Kpi
          icon={<Hash className="h-4 w-4" />}
          label="Tokens"
          value={formatTokens(data.total_tokens)}
        />
        <Kpi
          icon={<Cpu className="h-4 w-4" />}
          label="Avg duration"
          value={data.avg_duration_ms ? formatDuration(data.avg_duration_ms) : "—"}
        />
        <Kpi
          icon={<Clock className="h-4 w-4" />}
          label="Avg cycle time"
          value={
            data.avg_cycle_time_ms
              ? formatDuration(data.avg_cycle_time_ms)
              : "—"
          }
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Cost over time">
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={data.buckets} margin={{ left: -16, right: 8, top: 8 }}>
              <defs>
                <linearGradient id="cost" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#22A06B" stopOpacity={0.4} />
                  <stop offset="100%" stopColor="#22A06B" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" {...AXIS} tickFormatter={shortDate} />
              <YAxis {...AXIS} width={48} tickFormatter={(v) => `$${v}`} />
              <Tooltip {...TOOLTIP} formatter={(v: number) => formatCost(v)} />
              <Area
                type="monotone"
                dataKey="cost_usd"
                name="Cost"
                stroke="#22A06B"
                fill="url(#cost)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Runs over time">
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={data.buckets} margin={{ left: -24, right: 8, top: 8 }}>
              <defs>
                <linearGradient id="runs" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#6E5DC6" stopOpacity={0.4} />
                  <stop offset="100%" stopColor="#6E5DC6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" {...AXIS} tickFormatter={shortDate} />
              <YAxis {...AXIS} width={32} allowDecimals={false} />
              <Tooltip {...TOOLTIP} />
              <Area
                type="monotone"
                dataKey="runs"
                name="Runs"
                stroke="#6E5DC6"
                fill="url(#runs)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Runs by status">
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie
                data={statusData}
                dataKey="value"
                nameKey="name"
                innerRadius={55}
                outerRadius={85}
                paddingAngle={2}
              >
                {statusData.map((s) => (
                  <Cell
                    key={s.name}
                    fill={STATUS_COLORS[s.name] ?? "#8590A2"}
                  />
                ))}
              </Pie>
              <Tooltip {...TOOLTIP} />
              <Legend {...LEGEND} />
            </PieChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Runs by agent">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              data={agentData}
              layout="vertical"
              margin={{ left: 8, right: 16, top: 8 }}
            >
              <XAxis type="number" {...AXIS} allowDecimals={false} />
              <YAxis
                type="category"
                dataKey="name"
                {...AXIS}
                width={110}
                tickFormatter={(v: string) =>
                  v.length > 14 ? `${v.slice(0, 14)}…` : v
                }
              />
              <Tooltip {...TOOLTIP} cursor={{ fill: "rgba(148,163,184,0.1)" }} />
              <Bar dataKey="runs" name="Runs" fill="#388BFF" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>
      </div>
    </div>
  );
}

const AXIS = {
  stroke: "#8590A2",
  fontSize: 11,
  tickLine: false,
  axisLine: false,
} as const;

const TOOLTIP = {
  contentStyle: {
    background: "hsl(var(--surface-1, 222 18% 12%))",
    border: "1px solid rgba(148,163,184,0.25)",
    borderRadius: 8,
    fontSize: 12,
  },
  labelStyle: { color: "#8590A2" },
} as const;

const LEGEND = {
  iconType: "circle",
  wrapperStyle: { fontSize: 12 },
} as const;

function shortDate(d: string): string {
  // "2026-06-07" -> "06-07"
  return d.length >= 10 ? d.slice(5) : d;
}

function Kpi({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3 shadow-raised">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="text-muted-foreground">{icon}</span>
        {label}
      </div>
      <div className="mt-1.5 font-mono text-xl font-semibold tabular text-foreground">
        {value}
      </div>
    </div>
  );
}

function Panel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-raised">
      <h2 className="mb-3 text-sm font-semibold text-foreground">{title}</h2>
      {children}
    </div>
  );
}

function Select({
  value,
  onChange,
  options,
  ariaLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  ariaLabel: string;
}) {
  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="cursor-pointer rounded border border-input bg-surface-2 px-2.5 py-1.5 text-xs text-foreground outline-none transition-colors hover:border-border-strong focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-ring/30"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function LoadingSkeleton() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-xl" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-64 rounded-xl" />
        ))}
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}
