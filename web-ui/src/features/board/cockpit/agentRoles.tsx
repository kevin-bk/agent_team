/**
 * Helpers to name the AIs staffing a goal's loop roles (planner / builder /
 * critic) so a human can see *which* agent is doing *what* — e.g. "Codex builds,
 * Claude reviews". Resolves a run's `agent_alias` back to its display name and
 * official brand logomark.
 */
import { useMemo } from "react";
import { agentBrand } from "@/components/brandIcons";
import { AgentGlyph, CliAgentGlyph } from "@/components/icons";
import { cn } from "@/lib/utils";
import type { AgentDTO } from "@/api/types";

export interface ResolvedAgent {
  alias: string;
  name: string;
  model?: string | null;
}

/** Index agents + CLI targets by their alias for O(1) lookup by `agent_alias`. */
export function useAgentIndex(
  agents: AgentDTO[],
  cliAgents: AgentDTO[],
): (alias: string | null | undefined) => ResolvedAgent | null {
  const byId = useMemo(() => {
    const m = new Map<string, AgentDTO>();
    for (const a of [...agents, ...cliAgents]) m.set(a.id, a);
    return m;
  }, [agents, cliAgents]);

  return (alias) => {
    if (!alias) return null;
    const hit = byId.get(alias);
    if (hit) return { alias, name: hit.display_name, model: hit.model };
    // Unknown alias: still give it a readable name (e.g. "cli:codex" → "Codex").
    const fallback = alias.startsWith("cli:")
      ? alias.slice(4).replace(/^\w/, (c) => c.toUpperCase())
      : alias;
    return { alias, name: fallback };
  };
}

/** A small brand-tinted square holding the agent's logo (or a generic glyph). */
export function AgentLogo({
  alias,
  model,
  className,
  glyphClassName,
}: {
  alias: string;
  model?: string | null;
  className?: string;
  glyphClassName?: string;
}) {
  const brand = agentBrand({ id: alias, model });
  const isCli = alias.startsWith("cli:");
  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center rounded",
        brand?.badge ??
          (isCli
            ? "bg-slate-200 text-slate-700 dark:bg-slate-500/20 dark:text-slate-200"
            : "bg-primary/10 text-primary"),
        className ?? "h-5 w-5",
      )}
    >
      {brand ? (
        <brand.Logo className={glyphClassName ?? "h-3 w-3"} />
      ) : isCli ? (
        <CliAgentGlyph className={glyphClassName ?? "h-3 w-3"} strokeWidth={2.25} />
      ) : (
        <AgentGlyph className={glyphClassName ?? "h-3 w-3"} strokeWidth={2.25} />
      )}
    </span>
  );
}

/** Inline "[logo] Name" chip for an agent. */
export function AgentInline({
  agent,
  className,
}: {
  agent: ResolvedAgent;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <AgentLogo alias={agent.alias} model={agent.model} />
      <span className="truncate font-medium text-foreground">{agent.name}</span>
    </span>
  );
}

export interface LoopRole {
  key: "planner" | "generator" | "evaluator";
  label: string;
  agent: ResolvedAgent | null;
}

/**
 * A compact roster of the AIs on this goal — one chip per staffed role, with the
 * currently-running role highlighted. Renders nothing until at least one role
 * has a known agent.
 */
export function AgentRoster({
  roles,
  activeRole,
}: {
  roles: LoopRole[];
  activeRole?: string | null;
}) {
  const staffed = roles.filter((r) => r.agent);
  if (staffed.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {staffed.map((r) => {
        const active = activeRole === r.key;
        return (
          <span
            key={r.key}
            className={cn(
              "inline-flex items-center gap-2 rounded-md border px-2 py-1 text-[12px]",
              active
                ? "border-emerald-300/60 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10"
                : "border-border bg-card",
            )}
          >
            <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {r.label}
            </span>
            {r.agent && <AgentInline agent={r.agent} />}
            {active && (
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}
