import { Bot, Sparkles, TerminalSquare } from "@/components/icons";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  useAgents,
  useCliTargets,
  useSkills,
  useUpdateBoard,
} from "@/api/hooks";
import type { BoardAgentMcp, BoardDTO } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { statusColor } from "./statusColor";

/**
 * Staff a board with agents. Kept separate from {@link BoardSettingsDialog} so
 * the (potentially long) agent roster has its own focused dialog, reachable via
 * its own button in the board header.
 */
export function BoardAgentsDialog({
  board,
  open,
  onClose,
}: {
  board: BoardDTO;
  open: boolean;
  onClose: () => void;
}) {
  const update = useUpdateBoard(board.id);
  const agents = useAgents();
  const cliTargets = useCliTargets();
  const skills = useSkills();
  const [agentIds, setAgentIds] = useState<string[]>(board.agent_ids ?? []);
  const [cliIds, setCliIds] = useState<string[]>(board.cli_target_ids ?? []);
  const [skillIds, setSkillIds] = useState<string[]>(board.skill_ids ?? []);
  // Per-CLI-agent MCP config kept as editable raw text (the `mcpServers` map),
  // keyed by the `cli:<engine>` alias; parsed on save.
  const [mcpText, setMcpText] = useState<Record<string, string>>({});

  useEffect(() => {
    if (open) {
      setAgentIds(board.agent_ids ?? []);
      setCliIds(board.cli_target_ids ?? []);
      setSkillIds(board.skill_ids ?? []);
      setMcpText(serializeAgentMcp(board.agent_mcp));
    }
  }, [open, board]);

  const toggleAgent = (id: string) =>
    setAgentIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
    );

  const toggleCli = (id: string) =>
    setCliIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
    );

  const toggleSkill = (name: string) =>
    setSkillIds((ids) =>
      ids.includes(name) ? ids.filter((x) => x !== name) : [...ids, name],
    );

  const save = async () => {
    let agentMcp: BoardAgentMcp;
    try {
      // Only persist MCP for CLI agents that are still enabled on the board.
      agentMcp = parseAgentMcp(mcpText, cliIds);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Invalid MCP config");
      return;
    }
    try {
      await update.mutateAsync({
        agent_ids: agentIds,
        cli_target_ids: cliIds,
        skill_ids: skillIds,
        agent_mcp: agentMcp,
      });
      toast.success("Board agents updated");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update agents");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="grid max-h-[88vh] w-full max-w-3xl grid-rows-[auto_minmax(0,1fr)_auto]">
        <DialogHeader>
          <DialogTitle>Board agents</DialogTitle>
        </DialogHeader>

        <div className="grid gap-4 overflow-y-auto pr-1">
        <div className="grid gap-1.5 pt-1">
          <span className="text-[12.5px] text-muted-foreground/80">
            Pick which agents staff this board — only the selected ones appear as
            threads inside its tasks.
          </span>
          {agents.isLoading ? (
            <div className="flex items-center gap-1.5 py-2 text-xs text-muted-foreground">
              <Spinner className="h-3 w-3" /> loading…
            </div>
          ) : (agents.data ?? []).length === 0 ? (
            <div className="py-2 text-[12.5px] text-muted-foreground">
              No agents are registered yet.
            </div>
          ) : (
            <div className="mt-1 grid gap-1 sm:grid-cols-2">
              {(agents.data ?? []).map((a) => {
                const c = statusColor(a.id);
                const checked = agentIds.includes(a.id);
                return (
                  <label
                    key={a.id}
                    className={cn(
                      "flex cursor-pointer items-center gap-2.5 rounded py-1.5 pl-2.5 pr-3 transition-colors",
                      checked
                        ? "bg-primary/10 hover:bg-primary/15"
                        : "bg-surface-1 hover:bg-surface-3",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleAgent(a.id)}
                      className="h-3.5 w-3.5 accent-primary"
                    />
                    <span
                      className={cn(
                        "flex h-6 w-6 shrink-0 items-center justify-center rounded-md",
                        c.soft,
                      )}
                    >
                      <Bot className="h-3.5 w-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          "block truncate text-[13px] font-medium",
                          checked ? "text-primary" : "text-foreground",
                        )}
                      >
                        {a.display_name}
                      </span>
                      <span className="block truncate text-[11.5px] text-muted-foreground">
                        {a.model ?? "agent"}
                      </span>
                    </span>
                    {!a.enabled && (
                      <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
                        Disabled
                      </span>
                    )}
                  </label>
                );
              })}
              <span className="pt-0.5 text-[12px] text-muted-foreground sm:col-span-2">
                {agentIds.length === 0
                  ? "No agents selected — tasks on this board won't show any agent."
                  : `${agentIds.length} agent${agentIds.length === 1 ? "" : "s"} selected.`}
              </span>
            </div>
          )}
        </div>

        <div className="grid gap-1.5 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            Direct CLI
          </span>
          <span className="text-[12.5px] text-muted-foreground/80">
            Chat straight with a coding CLI (no LLM orchestrator). Only the
            selected engines appear as threads inside this board's tasks.
          </span>
          {cliTargets.isLoading ? (
            <div className="flex items-center gap-1.5 py-2 text-xs text-muted-foreground">
              <Spinner className="h-3 w-3" /> loading…
            </div>
          ) : (
            <div className="mt-1 grid gap-1 sm:grid-cols-2">
              {(cliTargets.data ?? []).map((t) => {
                const checked = cliIds.includes(t.id);
                return (
                  <label
                    key={t.id}
                    className={cn(
                      "flex cursor-pointer items-center gap-2.5 rounded py-1.5 pl-2.5 pr-3 transition-colors",
                      checked
                        ? "bg-primary/10 hover:bg-primary/15"
                        : "bg-surface-1 hover:bg-surface-3",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleCli(t.id)}
                      className="h-3.5 w-3.5 accent-primary"
                    />
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-3 text-foreground">
                      <TerminalSquare className="h-3.5 w-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          "block truncate text-[13px] font-medium",
                          checked ? "text-primary" : "text-foreground",
                        )}
                      >
                        {t.label}
                      </span>
                      <span className="block truncate text-[11.5px] text-muted-foreground">
                        {t.available ? "no LLM" : "not installed on host"}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </div>

        {cliIds.length > 0 && (
          <div className="grid gap-1.5 border-t border-border pt-3">
            <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
              MCP servers (per CLI agent)
            </span>
            <span className="text-[12.5px] text-muted-foreground/80">
              Give each enabled CLI agent its own MCP servers. Paste the{" "}
              <code className="rounded bg-surface-3 px-1 py-0.5 text-[11px]">
                mcpServers
              </code>{" "}
              map as JSON. Leave blank for none.
            </span>
            <div className="mt-1 grid gap-2.5 sm:grid-cols-2">
              {(cliTargets.data ?? [])
                .filter((t) => cliIds.includes(t.id))
                .map((t) => (
                  <div key={t.id} className="grid gap-1">
                    <span className="text-[12px] font-medium text-foreground">
                      {t.label}
                    </span>
                    <textarea
                      value={mcpText[t.id] ?? ""}
                      onChange={(e) =>
                        setMcpText((prev) => ({ ...prev, [t.id]: e.target.value }))
                      }
                      spellCheck={false}
                      rows={4}
                      placeholder={MCP_PLACEHOLDER}
                      className="w-full resize-y rounded border border-border bg-surface-1 px-2.5 py-1.5 font-mono text-[11.5px] leading-relaxed text-foreground outline-none focus:border-primary"
                    />
                  </div>
                ))}
            </div>
          </div>
        )}

        <div className="grid gap-1.5 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
            Skills
          </span>
          <span className="text-[12.5px] text-muted-foreground/80">
            Skill packs are copied into each task workspace for the direct CLI
            agents (Claude / Cursor read them natively; all engines see them in
            the task brief).
          </span>
          {skills.isLoading ? (
            <div className="flex items-center gap-1.5 py-2 text-xs text-muted-foreground">
              <Spinner className="h-3 w-3" /> loading…
            </div>
          ) : (skills.data ?? []).length === 0 ? (
            <div className="py-2 text-[12.5px] text-muted-foreground">
              No skill packs are available. Add some under Skill Packs first.
            </div>
          ) : (
            <div className="mt-1 grid gap-1 sm:grid-cols-2">
              {(skills.data ?? []).map((s) => {
                const checked = skillIds.includes(s.name);
                return (
                  <label
                    key={s.name}
                    className={cn(
                      "flex cursor-pointer items-center gap-2.5 rounded py-1.5 pl-2.5 pr-3 transition-colors",
                      checked
                        ? "bg-primary/10 hover:bg-primary/15"
                        : "bg-surface-1 hover:bg-surface-3",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleSkill(s.name)}
                      className="h-3.5 w-3.5 accent-primary"
                    />
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-3 text-foreground">
                      <Sparkles className="h-3.5 w-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          "block truncate text-[13px] font-medium",
                          checked ? "text-primary" : "text-foreground",
                        )}
                      >
                        {s.name}
                        {s.version ? (
                          <span className="ml-1 text-[11px] font-normal text-muted-foreground">
                            v{s.version}
                          </span>
                        ) : null}
                      </span>
                      <span className="block truncate text-[11.5px] text-muted-foreground">
                        {s.description}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </div>
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={update.isPending}>
            Cancel
          </Button>
          <Button onClick={save} disabled={update.isPending}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const MCP_PLACEHOLDER = `{
  "context7": {
    "url": "https://mcp.context7.com/sse",
    "auth": "<token>"
  }
}`;

/** Turn the board's stored MCP config into an editable text map (alias → JSON). */
function serializeAgentMcp(agentMcp?: BoardAgentMcp): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [alias, cfg] of Object.entries(agentMcp ?? {})) {
    const servers = cfg?.mcpServers ?? {};
    if (Object.keys(servers).length > 0) {
      out[alias] = JSON.stringify(servers, null, 2);
    }
  }
  return out;
}

/**
 * Parse the per-alias text editors back into a {@link BoardAgentMcp}. Only
 * aliases still enabled (in `cliIds`) with non-empty, valid JSON are kept; an
 * invalid entry throws so the caller can surface a clear error.
 */
function parseAgentMcp(
  text: Record<string, string>,
  cliIds: string[],
): BoardAgentMcp {
  const out: BoardAgentMcp = {};
  for (const alias of cliIds) {
    const raw = (text[alias] ?? "").trim();
    if (!raw) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new Error(`MCP config for ${alias} is not valid JSON`);
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error(`MCP config for ${alias} must be a JSON object`);
    }
    out[alias] = { mcpServers: parsed as BoardAgentMcp[string]["mcpServers"] };
  }
  return out;
}
