import { ArrowDown, ArrowUp, Plus, Trash2 } from "@/components/icons";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { useUpdateBoard } from "@/api/hooks";
import type { BoardColumn, BoardDTO } from "@/api/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

function slugifyKey(name: string): string {
  return (
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "column"
  );
}

function uniqueKey(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}-${i}`)) i += 1;
  return `${base}-${i}`;
}

export function BoardSettingsDialog({
  board,
  canArchive,
  taskCounts,
  open,
  onClose,
  onArchived,
}: {
  board: BoardDTO;
  canArchive: boolean;
  taskCounts: Record<string, number>;
  open: boolean;
  onClose: () => void;
  onArchived: () => void;
}) {
  const update = useUpdateBoard(board.id);
  const confirm = useConfirm();
  const [name, setName] = useState(board.name);
  const [description, setDescription] = useState(board.description ?? "");
  const [columns, setColumns] = useState<BoardColumn[]>(board.columns);

  // Reset the draft whenever the dialog (re)opens for a board.
  useEffect(() => {
    if (open) {
      setName(board.name);
      setDescription(board.description ?? "");
      setColumns(board.columns);
    }
  }, [open, board]);

  const renameColumn = (idx: number, value: string) =>
    setColumns((cols) =>
      cols.map((c, i) => (i === idx ? { ...c, name: value } : c)),
    );

  const moveColumn = (idx: number, dir: -1 | 1) =>
    setColumns((cols) => {
      const next = [...cols];
      const target = idx + dir;
      if (target < 0 || target >= next.length) return cols;
      [next[idx], next[target]] = [next[target], next[idx]];
      return next;
    });

  const removeColumn = (idx: number) =>
    setColumns((cols) => cols.filter((_, i) => i !== idx));

  const addColumn = () =>
    setColumns((cols) => {
      const taken = new Set(cols.map((c) => c.key));
      const key = uniqueKey("column", taken);
      return [...cols, { key, name: "New column" }];
    });

  const save = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      toast.error("Board name is required");
      return;
    }
    const cleaned: BoardColumn[] = [];
    const taken = new Set<string>();
    for (const col of columns) {
      const colName = col.name.trim();
      if (!colName) {
        toast.error("Every column needs a name");
        return;
      }
      // Keep stable keys for existing columns (tasks reference column.key as
      // their status); only mint a key for freshly-added ones.
      let key = col.key;
      if (!key || taken.has(key)) key = uniqueKey(slugifyKey(colName), taken);
      taken.add(key);
      cleaned.push({ key, name: colName });
    }
    if (cleaned.length === 0) {
      toast.error("A board needs at least one column");
      return;
    }
    try {
      await update.mutateAsync({
        name: trimmedName,
        description: description.trim() || null,
        columns: cleaned,
      });
      toast.success("Board updated");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update board");
    }
  };

  const archive = async () => {
    const ok = await confirm({
      title: `Archive “${board.name}”?`,
      description:
        "The board is hidden from everyone but its data is kept. This can be undone from the database.",
      confirmLabel: "Archive board",
      tone: "danger",
    });
    if (!ok) return;
    try {
      await update.mutateAsync({ archived: true });
      toast.success("Board archived");
      onArchived();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to archive board");
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Board settings</DialogTitle>
        </DialogHeader>

        <div className="grid max-h-[60vh] gap-4 overflow-y-auto pr-1 pt-1">
          <label className="grid gap-1.5">
            <span className="text-[13px] font-medium text-muted-foreground">
              Name
            </span>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </label>

          <label className="grid gap-1.5">
            <span className="text-[13px] font-medium text-muted-foreground">
              Description
            </span>
            <Textarea
              value={description}
              placeholder="Optional"
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>

          <div className="grid gap-1.5">
            <span className="text-[13px] font-medium text-muted-foreground">
              Columns
            </span>
            <span className="text-[12.5px] text-muted-foreground/80">
              Columns map 1:1 to the board's statuses. Reorder, rename, or
              remove empty ones.
            </span>
            <div className="mt-1 grid gap-1">
              {columns.map((col, idx) => {
                const count = taskCounts[col.key] ?? 0;
                const canDelete = count === 0 && columns.length > 1;
                return (
                  <div
                    key={col.key}
                    className="group/col flex items-center gap-2 rounded bg-surface-1 py-1.5 pl-1.5 pr-2 transition-colors hover:bg-surface-3"
                  >
                    <div className="flex flex-col">
                      <button
                        type="button"
                        aria-label="Move up"
                        disabled={idx === 0}
                        onClick={() => moveColumn(idx, -1)}
                        className="rounded p-0.5 text-muted-foreground hover:text-primary disabled:opacity-30 disabled:hover:text-muted-foreground"
                      >
                        <ArrowUp className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        aria-label="Move down"
                        disabled={idx === columns.length - 1}
                        onClick={() => moveColumn(idx, 1)}
                        className="rounded p-0.5 text-muted-foreground hover:text-primary disabled:opacity-30 disabled:hover:text-muted-foreground"
                      >
                        <ArrowDown className="h-3.5 w-3.5" />
                      </button>
                    </div>
                    <Input
                      value={col.name}
                      onChange={(e) => renameColumn(idx, e.target.value)}
                      className="h-8 flex-1 font-medium"
                    />
                    <span className="w-16 shrink-0 text-right text-[12px] text-muted-foreground tabular-nums">
                      {count} task{count === 1 ? "" : "s"}
                    </span>
                    <button
                      type="button"
                      aria-label="Delete column"
                      disabled={!canDelete}
                      title={
                        canDelete
                          ? "Delete column"
                          : count > 0
                            ? "Move/clear its tasks first"
                            : "A board needs at least one column"
                      }
                      onClick={() => removeColumn(idx)}
                      className={cn(
                        "rounded p-1.5 transition-colors",
                        canDelete
                          ? "text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                          : "cursor-not-allowed text-border-strong",
                      )}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                );
              })}
            </div>
            <button
              type="button"
              onClick={addColumn}
              className="flex h-8 items-center gap-1.5 self-start rounded px-2.5 text-[14px] font-medium text-primary transition-colors duration-100 hover:bg-primary/10"
            >
              <Plus className="h-4 w-4" /> Add column
            </button>
          </div>
        </div>

        <DialogFooter className="flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          {canArchive ? (
            <Button
              variant="ghost"
              onClick={archive}
              disabled={update.isPending}
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
            >
              Archive board
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose} disabled={update.isPending}>
              Cancel
            </Button>
            <Button onClick={save} disabled={update.isPending}>
              Save changes
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
