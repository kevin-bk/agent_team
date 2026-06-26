import { Bot, Sparkles, TerminalSquare } from "@/components/icons";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  useAgents,
  useCliTargets,
  useSkills,
  useUpdateBoard,
} from "@/api/hooks";
import type { BoardDTO } from "@/api/types";
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

  useEffect(() => {
    if (open) {
      setAgentIds(board.agent_ids ?? []);
      setCliIds(board.cli_target_ids ?? []);
      setSkillIds(board.skill_ids ?? []);
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
    try {
      await update.mutateAsync({
        agent_ids: agentIds,
        cli_target_ids: cliIds,
        skill_ids: skillIds,
      });
      toast.success("Board agents updated");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update agents");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Board agents</DialogTitle>
        </DialogHeader>

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
            <div className="mt-1 grid max-h-[55vh] gap-1 overflow-y-auto pr-1">
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
              <span className="pt-0.5 text-[12px] text-muted-foreground">
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
            <div className="mt-1 grid gap-1">
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
            <div className="mt-1 grid max-h-[40vh] gap-1 overflow-y-auto pr-1">
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
