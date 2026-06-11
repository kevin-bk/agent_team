import { Trash2, UserPlus } from "@/components/icons";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useAddBoardMember,
  useBoardMembers,
  useRemoveBoardMember,
  useUsers,
} from "@/api/hooks";
import type { BoardRole } from "@/api/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { JiraAvatar } from "@/components/jira";
import { UserSelect } from "@/components/UserSelect";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SelectMenu } from "@/components/ui/select-menu";
import { Spinner } from "@/components/ui/spinner";

const ROLES: BoardRole[] = ["viewer", "editor", "owner"];

export function MembersDialog({
  boardId,
  canManage,
  open,
  onClose,
}: {
  boardId: string;
  canManage: boolean;
  open: boolean;
  onClose: () => void;
}) {
  const members = useBoardMembers(open ? boardId : undefined);
  const directory = useUsers();
  const add = useAddBoardMember(boardId);
  const remove = useRemoveBoardMember(boardId);
  const confirm = useConfirm();

  const [userId, setUserId] = useState<string | null>(null);
  const [role, setRole] = useState<BoardRole>("editor");

  // Offer only users who aren't already on the board.
  const candidates = useMemo(() => {
    const existing = new Set((members.data ?? []).map((m) => m.user_id));
    return (directory.data ?? [])
      .filter((u) => !existing.has(u.id))
      .map((u) => ({
        id: u.id,
        name: u.display_name || u.email || u.id,
        email: u.email,
        avatar: u.avatar_url,
      }));
  }, [directory.data, members.data]);

  const onAdd = async () => {
    if (!userId) return;
    try {
      await add.mutateAsync({ user_id: userId, role });
      toast.success("Member added");
      setUserId(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to add member");
    }
  };

  const onRemove = async (userId: string) => {
    const ok = await confirm({
      title: "Remove member?",
      description: `Remove ${userId} from this board.`,
      tone: "danger",
      confirmLabel: "Remove",
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(userId);
      toast.success("Member removed");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Board members</DialogTitle>
        </DialogHeader>

        {canManage && (
          <div className="flex items-end gap-2">
            <label className="grid flex-1 gap-1.5">
              <span className="text-xs font-medium text-muted-foreground">
                Add a person
              </span>
              <UserSelect
                options={candidates}
                value={userId}
                onChange={setUserId}
                placeholder="Search people…"
                loading={directory.isLoading}
              />
            </label>
            <SelectMenu
              value={role}
              onChange={(v) => setRole(v as BoardRole)}
              className="w-28"
              options={ROLES.map((r) => ({
                value: r,
                label: r.charAt(0).toUpperCase() + r.slice(1),
              }))}
            />
            <Button onClick={onAdd} disabled={add.isPending || !userId}>
              <UserPlus className="h-4 w-4" /> Add
            </Button>
          </div>
        )}

        <div className="mt-1 max-h-80 overflow-y-auto">
          {members.isLoading ? (
            <div className="flex justify-center py-6">
              <Spinner />
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {(members.data ?? []).map((m) => (
                <li
                  key={m.user_id}
                  className="flex items-center gap-3 py-2.5"
                >
                  <JiraAvatar
                    name={m.display_name || m.email || m.user_id}
                    src={m.avatar_url}
                    size={32}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {m.display_name || m.email || m.user_id}
                    </p>
                    {(m.email || m.display_name) && (
                      <p className="truncate text-xs text-muted-foreground">
                        {m.email ?? m.user_id}
                      </p>
                    )}
                  </div>
                  <span className="rounded-full bg-surface-3 px-2 py-0.5 text-[11px] text-muted-foreground">
                    {m.role}
                  </span>
                  {canManage && (
                    <button
                      type="button"
                      onClick={() => onRemove(m.user_id)}
                      aria-label={`Remove ${m.user_id}`}
                      className="rounded p-1 text-muted-foreground transition-colors hover:bg-surface-3 hover:text-destructive"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </li>
              ))}
              {(members.data ?? []).length === 0 && (
                <li className="py-6 text-center text-xs text-muted-foreground">
                  No members yet
                </li>
              )}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
