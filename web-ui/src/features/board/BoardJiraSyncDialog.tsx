import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useApi } from "@/api/ApiProvider";
import { qk } from "@/api/hooks";
import type { BoardDTO, JiraPreviewItem } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { IssueTypeIcon } from "@/components/jira/IssueTypeIcon";
import { cn } from "@/lib/utils";
import { PriorityIcon } from "./priority";

type Phase = "loading" | "review" | "running" | "done";

/**
 * Project import flow: preview the configured Jira project's issues (each marked
 * "New" or "Update"), let the user pick which to pull in, then import them one by
 * one with a live progress bar. Reuses the per-issue import endpoint so each
 * completion advances the bar.
 */
export function BoardJiraSyncDialog({
  board,
  open,
  onClose,
}: {
  board: BoardDTO;
  open: boolean;
  onClose: () => void;
}) {
  const { client } = useApi();
  const qc = useQueryClient();

  const [phase, setPhase] = useState<Phase>("loading");
  const [items, setItems] = useState<JiraPreviewItem[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [done, setDone] = useState(0);
  const [failed, setFailed] = useState(0);
  const [errors, setErrors] = useState<string[]>([]);
  const [quickKey, setQuickKey] = useState("");
  const [quickBusy, setQuickBusy] = useState(false);

  const loadPreview = useCallback(async () => {
    setPhase("loading");
    setDone(0);
    setFailed(0);
    setErrors([]);
    setQuickKey("");
    try {
      const res = await client.previewBoardJiraSync(board.id);
      setItems(res.items);
      setSelected(new Set(res.items.map((i) => i.jira_key)));
      setPhase("review");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load preview");
      onClose();
    }
  }, [client, board.id, onClose]);

  // Load the preview once per open. A ref guards against re-running when
  // `loadPreview`'s identity changes mid-import (board refetches from sync SSE
  // would otherwise reset the dialog back to the review list).
  const loadedForOpen = useRef(false);
  useEffect(() => {
    if (!open) {
      loadedForOpen.current = false;
      return;
    }
    if (loadedForOpen.current) return;
    loadedForOpen.current = true;
    void loadPreview();
  }, [open, loadPreview]);

  const allSelected =
    items.length > 0 && items.every((i) => selected.has(i.jira_key));

  const toggleOne = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(items.map((i) => i.jira_key)));

  const total = items.filter((i) => selected.has(i.jira_key)).length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  // Import a single issue straight from a typed key — works even for keys the
  // board filter would exclude from the list above.
  const quickImport = async () => {
    const key = quickKey.trim().toUpperCase();
    if (!key || quickBusy) return;
    setQuickBusy(true);
    try {
      await client.importIssueFromJira(board.id, key);
      void qc.invalidateQueries({ queryKey: qk.boardTasks(board.id) });
      toast.success(`Imported ${key}`);
      setQuickKey("");
      await loadPreview();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `Failed to import ${key}`);
    } finally {
      setQuickBusy(false);
    }
  };

  const run = async () => {
    const keys = items
      .filter((i) => selected.has(i.jira_key))
      .map((i) => i.jira_key);
    if (keys.length === 0) return;
    setPhase("running");
    setDone(0);
    setFailed(0);
    const errs: string[] = [];
    for (const key of keys) {
      try {
        await client.importIssueFromJira(board.id, key);
      } catch (err) {
        errs.push(`${key}: ${err instanceof Error ? err.message : "failed"}`);
        setFailed((n) => n + 1);
      }
      setDone((n) => n + 1);
    }
    setErrors(errs);
    void qc.invalidateQueries({ queryKey: qk.boardTasks(board.id) });
    setPhase("done");
    const okCount = keys.length - errs.length;
    if (errs.length > 0)
      toast.warning(`Imported ${okCount}, failed ${errs.length}`);
    else toast.success(`Imported ${okCount} issue${okCount === 1 ? "" : "s"}`);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>Import from Jira</DialogTitle>
        </DialogHeader>

        {phase === "loading" && (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
            <Spinner className="h-4 w-4" /> Loading issues from Jira…
          </div>
        )}

        {phase === "review" && (
          <>
            <div className="flex items-center gap-2 rounded-md border border-border bg-surface-1 p-2">
              <span className="shrink-0 text-[12.5px] font-medium text-muted-foreground">
                Import one by key
              </span>
              <Input
                value={quickKey}
                placeholder="e.g. CHIZY-123"
                disabled={quickBusy}
                onChange={(e) => setQuickKey(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void quickImport();
                  }
                }}
                className="h-8 flex-1"
              />
              <Button
                size="sm"
                variant="secondary"
                onClick={() => void quickImport()}
                disabled={quickBusy || !quickKey.trim()}
              >
                {quickBusy ? <Spinner className="h-4 w-4" /> : "Import"}
              </Button>
            </div>

            {items.length === 0 ? (
              <div className="py-6 text-center text-[13px] text-muted-foreground">
                No issues match the filter — import one by key above.
              </div>
            ) : (
              <>
                <label className="flex cursor-pointer items-center gap-2 pt-1 text-[13px] font-medium text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    className="h-3.5 w-3.5 accent-primary"
                  />
                  Select all ({items.length})
                </label>
                <div className="grid max-h-[55vh] gap-1 overflow-y-auto pr-1">
                {items.map((i) => {
                  const checked = selected.has(i.jira_key);
                  return (
                    <label
                      key={i.jira_key}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-[13px]",
                        checked
                          ? "bg-primary/10"
                          : "bg-surface-1 hover:bg-surface-3",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleOne(i.jira_key)}
                        className="h-3.5 w-3.5 accent-primary"
                      />
                      {/* Leading meta: New/Update tag, type glyph, priority glyph,
                          key — then title. */}
                      <span
                        className={cn(
                          "w-[52px] shrink-0 rounded px-1.5 py-0.5 text-center text-[10.5px] font-semibold uppercase tracking-[0.03em]",
                          i.exists
                            ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                            : "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
                        )}
                      >
                        {i.exists ? "Update" : "New"}
                      </span>
                      {i.status && (
                        <span className="max-w-[110px] shrink-0 truncate rounded bg-surface-3 px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">
                          {i.status}
                        </span>
                      )}
                      {i.task_type ? (
                        <IssueTypeIcon type={i.task_type} size={16} />
                      ) : (
                        <span className="inline-block h-4 w-4 shrink-0" />
                      )}
                      <span title={i.jira_priority ?? undefined}>
                        <PriorityIcon priority={i.priority} className="h-4 w-4" />
                      </span>
                      <span className="shrink-0 font-mono text-[12px] text-muted-foreground">
                        {i.jira_key}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-foreground">
                        {i.title}
                      </span>
                    </label>
                  );
                })}
                </div>
              </>
            )}
          </>
        )}

        {(phase === "running" || phase === "done") && (
          <div className="grid gap-3 py-4">
            <div className="flex items-center justify-between text-[13px] text-muted-foreground">
              <span>
                {phase === "done" ? "Done" : "Importing…"} {done}/{total}
              </span>
              {failed > 0 && (
                <span className="text-destructive">{failed} failed</span>
              )}
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-3">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
            {phase === "done" && errors.length > 0 && (
              <div className="max-h-40 overflow-y-auto rounded bg-surface-1 p-2 text-[12px] text-destructive">
                {errors.map((e, idx) => (
                  <div key={idx}>{e}</div>
                ))}
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          {phase === "review" && (
            <>
              <Button variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button onClick={run} disabled={total === 0}>
                Import {total} issue{total === 1 ? "" : "s"}
              </Button>
            </>
          )}
          {phase === "running" && (
            <Button disabled>
              <Spinner className="h-4 w-4" /> Importing…
            </Button>
          )}
          {phase === "done" && <Button onClick={onClose}>Close</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
