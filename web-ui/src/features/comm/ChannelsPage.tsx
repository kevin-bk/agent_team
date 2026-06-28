import { useState } from "react";
import { toast } from "sonner";
import { Pencil, Plus, Send, Trash2 } from "@/components/icons";
import {
  useCommConnectionMutations,
  useCommConnections,
  useMe,
} from "@/api/hooks";
import type { CommConnectionDTO } from "@/api/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { ConnectionDialog } from "@/features/comm/ConnectionDialog";

/** Admin-only registry of communication connections (Mattermost bots). */
export function ChannelsPage() {
  const me = useMe();
  const connections = useCommConnections();
  const { remove } = useCommConnectionMutations();
  const confirm = useConfirm();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<CommConnectionDTO | null>(null);

  if (me.data && !me.data.is_admin) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Communication channels are available to administrators only.
      </div>
    );
  }

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };
  const openEdit = (conn: CommConnectionDTO) => {
    setEditing(conn);
    setDialogOpen(true);
  };

  const doDelete = async (conn: CommConnectionDTO) => {
    const ok = await confirm({
      title: `Delete “${conn.name}”?`,
      description:
        "This removes the connection. Boards linked to it will stop sending notifications.",
      tone: "danger",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(conn.id);
      toast.success("Connection deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete");
    }
  };

  const list = connections.data ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <div className="flex items-center gap-2.5">
          <Send className="h-5 w-5 text-muted-foreground" />
          <div>
            <h1 className="text-[18px] font-semibold text-foreground">Channels</h1>
            <p className="text-[12.5px] text-muted-foreground">
              Connections (Mattermost, Slack) boards use to send task notifications.
            </p>
          </div>
        </div>
        <Button onClick={openCreate}>
          <Plus /> Add connection
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        {connections.isLoading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Spinner className="h-4 w-4" /> Loading…
          </div>
        ) : list.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center">
            <Send className="mx-auto h-8 w-8 text-muted-foreground/50" />
            <p className="mt-3 text-sm font-medium text-foreground">No connections yet</p>
            <p className="mt-1 text-[13px] text-muted-foreground">
              Add a Mattermost or Slack bot connection, then link it from a board's Channel
              settings.
            </p>
            <Button className="mt-4" onClick={openCreate}>
              <Plus /> Add connection
            </Button>
          </div>
        ) : (
          <div className="grid gap-2">
            {list.map((conn) => (
              <div
                key={conn.id}
                className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3"
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-surface-1">
                  <Send className="h-5 w-5 text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[14px] font-medium text-foreground">
                      {conn.name}
                    </span>
                    <Badge variant="outline">{conn.provider}</Badge>
                    {conn.has_token ? (
                      <Badge variant="success">Token set</Badge>
                    ) : (
                      <Badge variant="destructive">No token</Badge>
                    )}
                  </div>
                  <div className="mt-0.5 truncate text-[12.5px] text-muted-foreground">
                    {conn.server_url || "no server URL"}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12px] text-muted-foreground">
                    <span>
                      {conn.used_by_boards} board{conn.used_by_boards === 1 ? "" : "s"}
                    </span>
                    {conn.deep_link_base && <span>Links: {conn.deep_link_base}</span>}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => openEdit(conn)}
                    title="Edit"
                  >
                    <Pencil />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => doDelete(conn)}
                    title="Delete"
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <ConnectionDialog
        connection={editing}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  );
}
