import { cn } from "@/lib/utils";

/**
 * Jira-style avatar. Renders a photo when available, otherwise a coloured
 * initial chip whose hue is derived deterministically from the name so the
 * same person always gets the same colour (matching Jira's avatar behaviour).
 */

/** Atlassian-flavoured avatar background ramp (used for initial fallbacks). */
const AVATAR_COLORS = [
  "bg-[#0747A6] text-white", // blue
  "bg-[#403294] text-white", // purple
  "bg-[#006644] text-white", // green
  "bg-[#974F0C] text-white", // orange
  "bg-[#bf2600] text-white", // red
  "bg-[#008DA6] text-white", // teal
  "bg-[#5243AA] text-white", // violet
  "bg-[#172B4D] text-white", // navy
];

function colorFor(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export interface JiraAvatarProps {
  name?: string | null;
  src?: string | null;
  /** Pixel size of the square avatar. */
  size?: number;
  /** Render a 2px ring matching the surface behind it (avatar stacks). */
  ring?: boolean;
  rounded?: boolean;
  className?: string;
  title?: string;
}

export function JiraAvatar({
  name,
  src,
  size = 24,
  ring = false,
  rounded = true,
  className,
  title,
}: JiraAvatarProps) {
  const label = (name || "").trim() || "Unassigned";
  const dimension = { width: size, height: size };
  const shape = rounded ? "rounded-full" : "rounded";
  const ringCls = ring ? "ring-2 ring-card" : "";

  if (src) {
    return (
      <img
        src={src}
        alt={label}
        title={title ?? label}
        style={dimension}
        className={cn("shrink-0 object-cover", shape, ringCls, className)}
      />
    );
  }

  return (
    <span
      title={title ?? label}
      style={{ ...dimension, fontSize: Math.max(9, Math.round(size * 0.4)) }}
      className={cn(
        "flex shrink-0 select-none items-center justify-center font-semibold uppercase leading-none",
        shape,
        ringCls,
        name ? colorFor(label) : "border border-dashed border-border-strong bg-transparent text-muted-foreground",
        className,
      )}
    >
      {name ? initials(label) : "?"}
    </span>
  );
}

export interface AvatarGroupItem {
  id: string;
  name?: string | null;
  src?: string | null;
  /** Tooltip override (e.g. "Name\nemail"); falls back to `name`. */
  title?: string | null;
}

/**
 * Overlapping avatar stack with the signature Jira "lift on hover" interaction.
 * Shows up to `max` faces, then a `+N` chip.
 */
export function AvatarGroup({
  items,
  max = 5,
  size = 28,
  onClick,
  onItemClick,
  activeIds,
  className,
  emptyLabel,
}: {
  items: AvatarGroupItem[];
  max?: number;
  size?: number;
  /** Click handler for the whole stack (e.g. open a members dialog). */
  onClick?: () => void;
  /**
   * Per-avatar click handler. When provided, each face becomes individually
   * clickable (Jira-style assignee filtering) and `onClick` is ignored on the
   * faces; the active ones get a highlight ring while the rest dim.
   */
  onItemClick?: (id: string) => void;
  /** Ids that are currently "selected" — highlighted, others dimmed. */
  activeIds?: string[];
  className?: string;
  emptyLabel?: string;
}) {
  const shown = items.slice(0, max);
  const extra = items.length - shown.length;
  const interactive = !!onItemClick;
  const active = new Set(activeIds ?? []);
  const hasActive = active.size > 0;

  if (items.length === 0 && emptyLabel) {
    const Wrapper = onClick ? "button" : "div";
    return (
      <Wrapper
        type={onClick ? "button" : undefined}
        onClick={onClick}
        className={cn(
          "flex h-7 items-center rounded-full border border-dashed border-border-strong px-3 text-xs text-muted-foreground transition-colors hover:bg-accent",
          className,
        )}
      >
        {emptyLabel}
      </Wrapper>
    );
  }

  // Interactive (per-avatar) mode: a plain container holding avatar buttons —
  // avoids nesting buttons. Non-interactive mode keeps the original behaviour.
  if (interactive) {
    return (
      <div className={cn("flex items-center", className)}>
        {shown.map((m) => {
          const isActive = active.has(m.id);
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => onItemClick?.(m.id)}
              title={m.title || m.name || m.id}
              aria-pressed={isActive}
              className={cn(
                "-ml-1.5 rounded-full transition-all duration-100 first:ml-0 hover:-translate-y-1",
                isActive && "ring-2 ring-primary ring-offset-1 ring-offset-card",
                hasActive && !isActive && "opacity-40 hover:opacity-100",
              )}
            >
              <JiraAvatar
                name={m.name}
                src={m.src}
                size={size}
                ring
                title={m.title || m.name || undefined}
              />
            </button>
          );
        })}
        {extra > 0 && (
          <span
            style={{ width: size, height: size }}
            className="-ml-1.5 flex items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-muted-foreground ring-2 ring-card"
          >
            +{extra}
          </span>
        )}
      </div>
    );
  }

  const Wrapper = onClick ? "button" : "div";
  return (
    <Wrapper
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={cn("flex items-center", onClick && "cursor-pointer", className)}
    >
      {shown.map((m) => (
        <span
          key={m.id}
          className="-ml-1.5 transition-transform duration-100 first:ml-0 hover:-translate-y-1"
        >
          <JiraAvatar
            name={m.name}
            src={m.src}
            size={size}
            ring
            title={m.title || m.name || undefined}
          />
        </span>
      ))}
      {extra > 0 && (
        <span
          style={{ width: size, height: size }}
          className="-ml-1.5 flex items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-muted-foreground ring-2 ring-card"
        >
          +{extra}
        </span>
      )}
    </Wrapper>
  );
}
