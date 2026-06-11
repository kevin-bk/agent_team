import { formatDistanceToNow } from "date-fns";

export function relativeTime(ms: number | null | undefined): string {
  if (!ms) return "";
  try {
    return formatDistanceToNow(new Date(ms), { addSuffix: true });
  } catch {
    return "";
  }
}

export function formatCost(usd: number | null | undefined): string {
  if (usd == null) return "$0.00";
  if (usd < 0.01 && usd > 0) return "<$0.01";
  return `$${usd.toFixed(usd < 1 ? 3 : 2)}`;
}

export function formatTokens(n: number | null | undefined): string {
  if (!n) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function formatBytes(n: number | null | undefined): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (!ms || ms < 0) return "";
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s % 60)}s`;
}
