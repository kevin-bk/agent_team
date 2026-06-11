import { cn } from "@/lib/utils";

/**
 * The official Atlassian Jira brand mark (multi-color, so it ignores
 * `currentColor` and keeps its blue gradients). Sized via `className`
 * (e.g. `h-4 w-4`) like the rest of our icon set.
 */
export function JiraIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 256 256"
      preserveAspectRatio="xMidYMid"
      role="img"
      aria-label="Jira"
      className={cn("shrink-0", className)}
    >
      <defs>
        <linearGradient
          id="jira-a"
          x1="98.031%"
          y1="0.161%"
          x2="58.888%"
          y2="40.766%"
        >
          <stop stopColor="#0052CC" offset="18%" />
          <stop stopColor="#2684FF" offset="100%" />
        </linearGradient>
        <linearGradient
          id="jira-b"
          x1="100.665%"
          y1="0.455%"
          x2="55.402%"
          y2="44.727%"
        >
          <stop stopColor="#0052CC" offset="18%" />
          <stop stopColor="#2684FF" offset="100%" />
        </linearGradient>
      </defs>
      <path
        d="M244.658 0H121.707a55.502 55.502 0 0 0 55.502 55.502h22.649V77.37c.02 30.625 24.841 55.447 55.466 55.467V10.666C255.324 4.777 250.55 0 244.658 0Z"
        fill="#2684FF"
      />
      <path
        d="M183.822 61.262H60.872c.019 30.625 24.84 55.447 55.466 55.466h22.649v21.938c.039 30.625 24.877 55.43 55.502 55.43V71.93c0-5.891-4.776-10.667-10.667-10.667Z"
        fill="url(#jira-a)"
      />
      <path
        d="M122.951 122.489H0c0 30.653 24.85 55.502 55.502 55.502h22.722v21.867c.02 30.597 24.798 55.408 55.396 55.466V133.156c0-5.891-4.776-10.667-10.669-10.667Z"
        fill="url(#jira-b)"
      />
    </svg>
  );
}
