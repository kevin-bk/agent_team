import { Check, ChevronDown, Search, UserRound, X } from "@/components/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

export interface UserOption {
  id: string;
  name: string;
  email?: string | null;
  avatar?: string | null;
}

/**
 * Reusable searchable people-picker (avatar + name + email).
 * Single-select; pass `allowUnassigned` to offer a clear option.
 * Self-contained popover (no extra deps) — reuse anywhere a user is chosen.
 */
export function UserSelect({
  options,
  value,
  onChange,
  placeholder = "Select a person…",
  allowUnassigned = false,
  unassignedLabel = "Unassigned",
  disabled = false,
  loading = false,
  className,
  align = "start",
}: {
  options: UserOption[];
  value: string | null;
  onChange: (id: string | null) => void;
  placeholder?: string;
  allowUnassigned?: boolean;
  unassignedLabel?: string;
  disabled?: boolean;
  loading?: boolean;
  className?: string;
  align?: "start" | "end";
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(
    () => options.find((o) => o.id === value) ?? null,
    [options, value],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        o.name.toLowerCase().includes(q) ||
        (o.email?.toLowerCase().includes(q) ?? false),
    );
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (id: string | null) => {
    onChange(id);
    setOpen(false);
    setQuery("");
  };

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex h-8 w-full items-center gap-2 rounded border border-input bg-card px-2.5 text-left text-sm text-foreground transition-colors duration-100 hover:border-border-strong focus-visible:border-[#4C9AFF] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60",
        )}
      >
        {selected ? (
          <>
            <Avatar option={selected} />
            <span className="min-w-0 flex-1 truncate">{selected.name}</span>
          </>
        ) : (
          <>
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-100 text-slate-400 dark:bg-surface-3">
              <UserRound className="h-3 w-3" />
            </span>
            <span className="min-w-0 flex-1 truncate text-slate-400">
              {placeholder}
            </span>
          </>
        )}
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div
          className={cn(
            "absolute z-50 mt-1 w-72 max-w-[min(20rem,90vw)] overflow-hidden rounded border border-border bg-popover shadow-overlay",
            align === "end" ? "right-0" : "left-0",
          )}
        >
          <div className="flex items-center gap-2 border-b border-slate-100 px-2.5 py-2 dark:border-border">
            <Search className="h-3.5 w-3.5 text-slate-400" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search name or email…"
              className="w-full bg-transparent text-sm text-slate-700 placeholder:text-slate-400 focus:outline-none dark:text-foreground"
            />
          </div>

          <div className="max-h-64 overflow-auto py-1 scrollbar-thin">
            {loading ? (
              <div className="flex items-center justify-center gap-1.5 py-5 text-xs text-slate-400">
                <Spinner className="h-3.5 w-3.5" /> loading…
              </div>
            ) : (
              <>
                {allowUnassigned && (
                  <Row
                    selected={value === null}
                    onClick={() => pick(null)}
                    leading={
                      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-100 text-slate-400 dark:bg-surface-3">
                        <X className="h-3 w-3" />
                      </span>
                    }
                    title={unassignedLabel}
                  />
                )}
                {filtered.map((o) => (
                  <Row
                    key={o.id}
                    selected={o.id === value}
                    onClick={() => pick(o.id)}
                    leading={<Avatar option={o} />}
                    title={o.name}
                    subtitle={o.email ?? undefined}
                  />
                ))}
                {filtered.length === 0 && (
                  <div className="px-3 py-5 text-center text-xs text-slate-400">
                    No people found
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({
  selected,
  onClick,
  leading,
  title,
  subtitle,
}: {
  selected: boolean;
  onClick: () => void;
  leading: React.ReactNode;
  title: string;
  subtitle?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors",
        selected ? "bg-primary/10" : "hover:bg-surface-1",
      )}
    >
      {leading}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium text-slate-800 dark:text-foreground">
          {title}
        </span>
        {subtitle && (
          <span className="block truncate text-[11px] text-slate-400">
            {subtitle}
          </span>
        )}
      </span>
      {selected && <Check className="h-4 w-4 shrink-0 text-brand-600" />}
    </button>
  );
}

function Avatar({ option }: { option: UserOption }) {
  if (option.avatar) {
    return (
      <img
        src={option.avatar}
        alt={option.name}
        className="h-5 w-5 shrink-0 rounded-full object-cover"
      />
    );
  }
  return (
    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-100 text-[9px] font-semibold uppercase text-brand-700 dark:bg-brand-500/20 dark:text-brand-300">
      {option.name.slice(0, 1)}
    </span>
  );
}
