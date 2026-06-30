import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  FileText,
  GitBranch,
  ListChecks,
  RefreshCw,
  Send,
  Sparkles,
  UserRound,
  X,
} from "@/components/icons";
import { useAddTaskJournalNote, useTaskJournal } from "@/api/hooks";
import type { JournalEntryDTO, TaskDTO } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Markdown } from "@/components/Markdown";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

const SEVERITY_META: Record<
  string,
  { label: string; chip: string; rail: string }
> = {
  info: {
    label: "Info",
    chip: "bg-surface-3 text-muted-foreground",
    rail: "bg-border-strong",
  },
  warning: {
    label: "Warning",
    chip: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
    rail: "bg-amber-400",
  },
  blocking: {
    label: "Blocking",
    chip: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
    rail: "bg-rose-500",
  },
};

const ACTOR_META: Record<
  string,
  { icon: typeof Bot; tone: string; label: string }
> = {
  human: {
    icon: UserRound,
    tone: "bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300",
    label: "Human",
  },
  agent: {
    icon: Bot,
    tone: "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300",
    label: "Agent",
  },
  system: {
    icon: Sparkles,
    tone: "bg-indigo-100 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300",
    label: "System",
  },
};

const TYPE_ICON: Record<string, typeof GitBranch> = {
  decision: GitBranch,
  assumption: ListChecks,
  risk: AlertTriangle,
  note: FileText,
};

const NOTE_TYPES = ["note", "decision", "assumption", "risk"] as const;
const NOTE_SEVERITIES = ["info", "warning", "blocking"] as const;

/** Render the small reference chips for an entry's `refs` dict. */
function RefChips({ refs }: { refs?: Record<string, unknown> }) {
  const items = useMemo(() => {
    const out: string[] = [];
    for (const [k, v] of Object.entries(refs ?? {})) {
      if (v == null || v === "") continue;
      if (Array.isArray(v)) {
        for (const item of v) if (item) out.push(`${k}: ${String(item)}`);
      } else {
        out.push(`${k}: ${String(v)}`);
      }
    }
    return out;
  }, [refs]);
  if (items.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {items.map((t) => (
        <span
          key={t}
          title={t}
          className="inline-flex max-w-[18rem] items-center truncate rounded border border-border bg-surface-1 px-1.5 py-0.5 font-mono text-[10.5px] text-muted-foreground"
        >
          {t}
        </span>
      ))}
    </div>
  );
}

function EntryRow({ entry }: { entry: JournalEntryDTO }) {
  const actor = ACTOR_META[entry.actor_type] ?? ACTOR_META.system;
  const ActorIcon = actor.icon;
  const sev = SEVERITY_META[entry.severity] ?? SEVERITY_META.info;
  const TypeIcon = TYPE_ICON[entry.type] ?? FileText;
  return (
    <li className="relative flex gap-3 pl-1">
      <span
        className={cn(
          "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
          actor.tone,
        )}
        title={actor.label}
      >
        <ActorIcon className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={cn("h-2 w-2 shrink-0 rounded-full", sev.rail)} />
          <span className="min-w-0 break-words text-[13.5px] font-semibold leading-snug text-foreground">
            {entry.title}
          </span>
          <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground/70">
            #{entry.seq}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className="inline-flex items-center gap-1 rounded bg-surface-3 px-1.5 py-0.5 font-medium">
            <TypeIcon className="h-3 w-3" /> {entry.type}
          </span>
          <span className="rounded bg-surface-3 px-1.5 py-0.5">{entry.phase}</span>
          {entry.severity !== "info" && (
            <span className={cn("rounded px-1.5 py-0.5 font-medium", sev.chip)}>
              {sev.label}
            </span>
          )}
          {entry.actor_id && (
            <span className="truncate font-mono opacity-80">{entry.actor_id}</span>
          )}
          {entry.created_at && (
            <span className="ml-auto shrink-0">{fmtTime(entry.created_at)}</span>
          )}
        </div>
        {entry.body && (
          <div className="prose-chat prose-note mt-1.5 max-w-none break-words text-[13px]">
            <Markdown taskId={entry.task_id}>{entry.body}</Markdown>
          </div>
        )}
        <RefChips refs={entry.refs} />
      </div>
    </li>
  );
}

/** Compact composer for a manual human journal note. */
function NoteComposer({ taskId }: { taskId: string }) {
  const add = useAddTaskJournalNote(taskId);
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [type, setType] = useState<(typeof NOTE_TYPES)[number]>("note");
  const [severity, setSeverity] =
    useState<(typeof NOTE_SEVERITIES)[number]>("info");

  const reset = () => {
    setTitle("");
    setBody("");
    setType("note");
    setSeverity("info");
    setOpen(false);
  };

  const submit = () => {
    const t = title.trim();
    if (!t || add.isPending) return;
    add.mutate(
      { title: t, body: body.trim(), type, severity },
      {
        onSuccess: reset,
        onError: () => toast.error("Could not add the journal note."),
      },
    );
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="h-9 w-full rounded border border-input bg-card px-3 text-left text-[13px] text-muted-foreground/70 transition-colors hover:border-border-strong"
      >
        Record a journal note…
      </button>
    );
  }

  return (
    <div className="rounded border border-input bg-card p-2.5">
      <Input
        autoFocus
        value={title}
        placeholder="Short summary (e.g. Chose Postgres over SQLite)"
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
        }}
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={2}
        placeholder="Details (optional)…"
        className="mt-2 block w-full resize-none rounded border border-input bg-transparent px-2.5 py-1.5 text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/40"
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Pills
          value={type}
          options={NOTE_TYPES as readonly string[]}
          onChange={(v) => setType(v as (typeof NOTE_TYPES)[number])}
        />
        <span className="text-border-strong">·</span>
        <Pills
          value={severity}
          options={NOTE_SEVERITIES as readonly string[]}
          onChange={(v) => setSeverity(v as (typeof NOTE_SEVERITIES)[number])}
        />
        <span className="ml-auto inline-flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={reset}>
            Cancel
          </Button>
          <Button size="sm" onClick={submit} disabled={!title.trim() || add.isPending}>
            <Send className="h-3.5 w-3.5" /> Add
          </Button>
        </span>
      </div>
    </div>
  );
}

function Pills({
  value,
  options,
  onChange,
}: {
  value: string;
  options: readonly string[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="inline-flex gap-0.5">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onChange(o)}
          className={cn(
            "rounded px-2 py-1 text-[11px] font-medium capitalize transition-colors",
            value === o
              ? "bg-primary/10 text-primary"
              : "text-muted-foreground hover:bg-surface-1 hover:text-foreground",
          )}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

type FilterKey = "type" | "phase" | "severity";

export function JournalPanel({
  task,
  canEdit,
}: {
  task: TaskDTO;
  canEdit: boolean;
}) {
  const journal = useTaskJournal(task.id);
  const entries = journal.data ?? [];
  const [filters, setFilters] = useState<Record<FilterKey, string>>({
    type: "",
    phase: "",
    severity: "",
  });

  // Distinct values present in the data drive the filter dropdowns.
  const options = useMemo(() => {
    const t = new Set<string>();
    const p = new Set<string>();
    for (const e of entries) {
      t.add(e.type);
      p.add(e.phase);
    }
    return { type: [...t].sort(), phase: [...p].sort() };
  }, [entries]);

  const visible = useMemo(
    () =>
      entries.filter(
        (e) =>
          (!filters.type || e.type === filters.type) &&
          (!filters.phase || e.phase === filters.phase) &&
          (!filters.severity || e.severity === filters.severity),
      ),
    [entries, filters],
  );

  const setFilter = (k: FilterKey, v: string) =>
    setFilters((prev) => ({ ...prev, [k]: prev[k] === v ? "" : v }));
  const hasFilters = filters.type || filters.phase || filters.severity;

  return (
    <>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <span className="flex h-6 w-6 items-center justify-center rounded bg-indigo-100 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300">
          <ListChecks className="h-3.5 w-3.5" />
        </span>
        <span className="text-sm font-semibold text-foreground">Journal</span>
        <span className="text-[11px] text-muted-foreground">
          · {entries.length} {entries.length === 1 ? "entry" : "entries"}
        </span>
        <button
          type="button"
          onClick={() => void journal.refetch()}
          aria-label="Refresh journal"
          title="Refresh"
          className="ml-auto rounded p-1 text-muted-foreground transition-colors hover:bg-surface-1 hover:text-foreground"
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", journal.isFetching && "animate-spin")}
          />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
        <div className="mx-auto max-w-5xl px-4 py-4 lg:px-6">
          {canEdit && (
            <div className="mb-5">
              <NoteComposer taskId={task.id} />
            </div>
          )}

          {/* Filters */}
          <div className="mb-4 flex flex-wrap items-center gap-2 text-[11px]">
            <FilterMenu
              label="Type"
              value={filters.type}
              options={options.type}
              onSelect={(v) => setFilter("type", v)}
            />
            <FilterMenu
              label="Phase"
              value={filters.phase}
              options={options.phase}
              onSelect={(v) => setFilter("phase", v)}
            />
            <FilterMenu
              label="Severity"
              value={filters.severity}
              options={[...NOTE_SEVERITIES]}
              onSelect={(v) => setFilter("severity", v)}
            />
            {hasFilters && (
              <button
                type="button"
                onClick={() => setFilters({ type: "", phase: "", severity: "" })}
                className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-muted-foreground transition-colors hover:bg-surface-1 hover:text-foreground"
              >
                <X className="h-3 w-3" /> Clear
              </button>
            )}
          </div>

          {journal.isLoading ? (
            <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <Spinner className="h-3.5 w-3.5" /> loading…
            </div>
          ) : visible.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
              <span className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <ListChecks className="h-6 w-6" />
              </span>
              <p className="text-sm font-medium text-foreground">
                {hasFilters ? "No entries match these filters" : "No journal yet"}
              </p>
              <p className="max-w-sm text-xs text-muted-foreground">
                The journal records key decisions, assumptions, risks and
                lifecycle events as the goal runs — a durable timeline that
                survives context compaction. Agents add notes automatically; you
                can add your own above.
              </p>
            </div>
          ) : (
            <ul className="flex flex-col gap-5">
              {visible.map((e) => (
                <EntryRow key={e.id} entry={e} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  );
}

function FilterMenu({
  label,
  value,
  options,
  onSelect,
}: {
  label: string;
  value: string;
  options: string[];
  onSelect: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (options.length === 0) return null;
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex items-center gap-1 rounded px-2 py-1 font-medium transition-colors",
          value
            ? "bg-primary/10 text-primary"
            : "bg-surface-1 text-muted-foreground hover:text-foreground",
        )}
      >
        {label}
        {value && <span className="capitalize">: {value}</span>}
        <ChevronDown className="h-3 w-3 opacity-70" />
      </button>
      {open && (
        <>
          <button
            type="button"
            aria-hidden
            tabIndex={-1}
            className="fixed inset-0 z-10 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div className="absolute left-0 z-20 mt-1 max-h-64 w-44 overflow-auto rounded border border-border bg-popover p-1 shadow-overlay scrollbar-thin">
            {options.map((o) => (
              <button
                key={o}
                type="button"
                onClick={() => {
                  onSelect(o);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-center rounded px-2 py-1.5 text-left text-[12px] capitalize transition-colors",
                  value === o
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-foreground hover:bg-surface-1",
                )}
              >
                {o}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
