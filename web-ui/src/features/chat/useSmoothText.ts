import { useEffect, useRef, useState } from "react";

/** Reveal the backlog of un-shown characters over roughly this window (ms). */
const CATCH_UP_MS = 150;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Smoothly reveal `target` like a typewriter, decoupled from the bursty arrival
 * of streamed tokens.
 *
 * Streamed tokens land in uneven bursts (one word, then a whole sentence), so
 * appending them straight to the DOM makes the text jump. While `streaming`,
 * the displayed text instead catches up to `target` over ~`CATCH_UP_MS` on a
 * `requestAnimationFrame` loop — faster as the backlog grows so it never lags
 * far behind — giving a steady cadence regardless of network jitter.
 *
 * It snaps to the full `target` immediately when streaming stops, when the user
 * prefers reduced motion, or when `target` no longer extends what is shown (a
 * new turn replaced the text), so the smoothing never hides or delays content.
 */
export function useSmoothText(target: string, streaming: boolean): string {
  const [displayed, setDisplayed] = useState(target);
  const displayedRef = useRef(displayed);
  displayedRef.current = displayed;
  const targetRef = useRef(target);
  targetRef.current = target;
  const rafRef = useRef<number | null>(null);
  const lastTsRef = useRef(0);

  useEffect(() => {
    const cancel = () => {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };

    // Snap (no animation) when not streaming, reduced motion is requested, or
    // the target diverges from what we've shown (reset / replaced text).
    if (
      !streaming ||
      prefersReducedMotion() ||
      !target.startsWith(displayedRef.current)
    ) {
      cancel();
      if (displayedRef.current !== target) setDisplayed(target);
      return cancel;
    }

    if (displayedRef.current.length >= target.length) return cancel;

    const step = (ts: number) => {
      const dt = lastTsRef.current ? ts - lastTsRef.current : 16;
      lastTsRef.current = ts;
      const shown = displayedRef.current;
      const goal = targetRef.current;
      if (shown.length >= goal.length) {
        rafRef.current = null;
        return;
      }
      const backlog = goal.length - shown.length;
      const reveal = Math.max(1, Math.ceil((backlog * dt) / CATCH_UP_MS));
      const next = goal.slice(0, shown.length + reveal);
      setDisplayed(next);
      rafRef.current =
        next.length < goal.length ? requestAnimationFrame(step) : null;
    };

    lastTsRef.current = 0;
    rafRef.current = requestAnimationFrame(step);
    return cancel;
  }, [target, streaming]);

  return streaming ? displayed : target;
}
