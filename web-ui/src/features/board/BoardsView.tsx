import { Plus } from "@/components/icons";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { useBoards, useCreateBoard } from "@/api/hooks";
import type { BoardDTO } from "@/api/types";
import { JiraAvatar, PageHeader } from "@/components/jira";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { statusColor } from "./statusColor";

const ROLE_BADGE: Record<string, string> = {
  owner:
    "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  editor: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  viewer:
    "bg-slate-100 text-slate-600 dark:bg-slate-500/15 dark:text-slate-300",
};

export function BoardsView() {
  const navigate = useNavigate();
  return <BoardList onOpen={(slug) => navigate(`/boards/${slug}`)} />;
}

function BoardList({ onOpen }: { onOpen: (slug: string) => void }) {
  const boards = useBoards();
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <div className="flex h-full flex-col bg-background">
      <PageHeader
        breadcrumbs={[{ label: "Projects" }, { label: "Boards" }]}
        title="Boards"
        subtitle="Kanban boards for your team & agents"
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" /> New board
          </Button>
        }
      />

      <div className="flex-1 overflow-y-auto px-8 py-6 scrollbar-thin">
        {boards.isLoading ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-28 w-full rounded-lg" />
            ))}
          </div>
        ) : boards.data && boards.data.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {boards.data.map((b) => (
              <BoardCard key={b.id} board={b} onOpen={onOpen} />
            ))}
          </div>
        ) : (
          <div className="mx-auto mt-10 flex max-w-sm flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border-strong bg-card p-10 text-center">
            <p className="text-sm font-medium text-foreground">No boards yet</p>
            <p className="text-[13px] text-muted-foreground">
              Create a board to start tracking work for your team & agents.
            </p>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" /> New board
            </Button>
          </div>
        )}
      </div>

      <CreateBoardDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(b) => {
          setCreateOpen(false);
          onOpen(b.slug);
        }}
      />
    </div>
  );
}

function BoardCard({
  board,
  onOpen,
}: {
  board: BoardDTO;
  onOpen: (slug: string) => void;
}) {
  const roleBadge = ROLE_BADGE[board.my_role ?? "viewer"] ?? ROLE_BADGE.viewer;
  return (
    <button
      type="button"
      onClick={() => onOpen(board.slug)}
      className="group flex flex-col gap-2.5 rounded border border-border bg-card p-4 text-left shadow-card transition-colors duration-100 hover:bg-surface-1"
    >
      <div className="flex items-center gap-2.5">
        <JiraAvatar name={board.name} size={36} rounded={false} />
        <span className="min-w-0 flex-1 truncate font-semibold text-foreground">
          {board.name}
        </span>
        {board.my_role && (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[11px] font-medium capitalize",
              roleBadge,
            )}
          >
            {board.my_role}
          </span>
        )}
      </div>
      <p className="line-clamp-2 min-h-8 text-xs text-muted-foreground">
        {board.description || "No description"}
      </p>
      <div className="flex flex-wrap gap-1">
        {board.columns.map((c) => {
          const cc = statusColor(c.key, c.name);
          return (
            <span
              key={c.key}
              className="inline-flex items-center gap-1 rounded bg-surface-1 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
            >
              <span className={cn("h-1.5 w-1.5 rounded-full", cc.dot)} />
              {c.name}
            </span>
          );
        })}
      </div>
    </button>
  );
}

function CreateBoardDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (board: BoardDTO) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useCreateBoard();

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      toast.error("Board name is required");
      return;
    }
    try {
      const board = await create.mutateAsync({
        name: trimmed,
        description: description.trim() || null,
      });
      toast.success(`Created “${board.name}”`);
      setName("");
      setDescription("");
      onCreated(board);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create board");
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New board</DialogTitle>
        </DialogHeader>
        <div className="grid gap-3">
          <label className="grid gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">Name</span>
            <Input
              value={name}
              autoFocus
              placeholder="Sprint board"
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </label>
          <label className="grid gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">
              Description
            </span>
            <Textarea
              value={description}
              placeholder="Optional"
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
        </div>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={create.isPending || !name.trim()}>
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
