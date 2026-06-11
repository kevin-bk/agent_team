import { cn } from "@/lib/utils";

/**
 * Placeholder shimmer for content that's still loading. Animation is dropped
 * under `prefers-reduced-motion`. Compose with width/height/rounding classes.
 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        "animate-pulse rounded-md bg-slate-200/70 motion-reduce:animate-none dark:bg-surface-2",
        className,
      )}
    />
  );
}
