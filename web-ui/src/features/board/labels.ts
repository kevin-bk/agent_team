/**
 * Colored label pills (plan 16 board mockup). Labels are free-form, so we map
 * each label string deterministically onto a small palette — the same label
 * always gets the same colour. A few common category names are pinned to the
 * exact mockup colours (Engineering=indigo, Marketing=rose, …).
 */
const PALETTE = [
  "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300",
  "bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
  "bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
  "bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  "bg-sky-50 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  "bg-fuchsia-50 text-fuchsia-700 dark:bg-fuchsia-500/15 dark:text-fuchsia-300",
  "bg-slate-100 text-slate-600 dark:bg-slate-500/15 dark:text-slate-300",
];

const PINNED: Record<string, number> = {
  engineering: 0,
  design: 1,
  product: 1,
  marketing: 2,
  devops: 3,
  ops: 3,
  analytics: 4,
  data: 4,
  qa: 7,
  test: 7,
};

export function labelClass(label: string): string {
  const key = label.trim().toLowerCase();
  if (key in PINNED) return PALETTE[PINNED[key]];
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  }
  return PALETTE[hash % PALETTE.length];
}
