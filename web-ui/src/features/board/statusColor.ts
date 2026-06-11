/**
 * Deterministic status colours for columns/cards (Jira-style colour rules).
 *
 * Columns are user-defined, so we map common status names onto a fixed palette
 * (todo=blue, in-progress=amber, review=violet, done=green, blocked=red …) and
 * fall back to a hashed palette for anything custom — the same key always gets
 * the same colour. Returns full Tailwind class strings (Tailwind needs literal
 * classes, so we can't build them dynamically).
 */

export interface StatusColor {
  /** Solid dot in the column header. */
  dot: string;
  /** Left accent border on a card. */
  stripe: string;
  /** Soft tinted count badge / chip. */
  soft: string;
}

const COLORS: Record<string, StatusColor> = {
  slate: {
    dot: "bg-slate-400",
    stripe: "border-l-slate-300 dark:border-l-slate-600",
    soft: "bg-slate-100 text-slate-600 dark:bg-slate-500/15 dark:text-slate-300",
  },
  sky: {
    dot: "bg-sky-500",
    stripe: "border-l-sky-400",
    soft: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  },
  amber: {
    dot: "bg-amber-500",
    stripe: "border-l-amber-400",
    soft: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  },
  violet: {
    dot: "bg-violet-500",
    stripe: "border-l-violet-400",
    soft: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
  },
  emerald: {
    dot: "bg-emerald-500",
    stripe: "border-l-emerald-400",
    soft: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  },
  rose: {
    dot: "bg-rose-500",
    stripe: "border-l-rose-400",
    soft: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
  },
  cyan: {
    dot: "bg-cyan-500",
    stripe: "border-l-cyan-400",
    soft: "bg-cyan-100 text-cyan-700 dark:bg-cyan-500/15 dark:text-cyan-300",
  },
  fuchsia: {
    dot: "bg-fuchsia-500",
    stripe: "border-l-fuchsia-400",
    soft: "bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-500/15 dark:text-fuchsia-300",
  },
  indigo: {
    dot: "bg-indigo-500",
    stripe: "border-l-indigo-400",
    soft: "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300",
  },
};

const FALLBACK = [
  "sky",
  "violet",
  "amber",
  "emerald",
  "rose",
  "cyan",
  "fuchsia",
  "indigo",
];

/** Keyword → colour rules, checked in order against the normalised status. */
const RULES: { match: RegExp; color: keyof typeof COLORS }[] = [
  { match: /(backlog|pending|icebox|idea|triage)/, color: "slate" },
  { match: /(todo|to do|open|new|ready|planned)/, color: "sky" },
  { match: /(progress|doing|active|wip|working|develop)/, color: "amber" },
  { match: /(review|qa|test|verify|approval)/, color: "violet" },
  { match: /(done|closed|complete|finish|ship|merged|resolved)/, color: "emerald" },
  { match: /(block|hold|stuck|waiting|paused)/, color: "rose" },
];

export function statusColor(key: string, name?: string): StatusColor {
  const hay = `${key} ${name ?? ""}`.toLowerCase().replace(/[-_]+/g, " ");
  for (const rule of RULES) {
    if (rule.match.test(hay)) return COLORS[rule.color];
  }
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  }
  return COLORS[FALLBACK[hash % FALLBACK.length]];
}
