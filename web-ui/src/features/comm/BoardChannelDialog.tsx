import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Send } from "@/components/icons";
import {
  useBoardChannel,
  useBoardChannelMutations,
  useBoardDeliveries,
  useCommEventTypes,
  useCommProviders,
} from "@/api/hooks";
import type { CommTagMode } from "@/api/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SelectMenu } from "@/components/ui/select-menu";
import { Spinner } from "@/components/ui/spinner";

const TAG_OPTIONS: { value: CommTagMode; label: string }[] = [
  { value: "assignee", label: "Mention the assignee" },
  { value: "creator", label: "Mention the creator" },
  { value: "none", label: "Don't mention anyone" },
];

function eventLabel(ev: string): string {
  return ev
    .replace(/^event_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Configure the single Mattermost channel a board posts notifications to. */
export function BoardChannelDialog({
  boardId,
  open,
  onClose,
}: {
  boardId: string;
  open: boolean;
  onClose: () => void;
}) {
  const data = useBoardChannel(open ? boardId : undefined);
  const eventTypes = useCommEventTypes();
  const providers = useCommProviders();
  const deliveries = useBoardDeliveries(open ? boardId : undefined);
  const { save, remove, test } = useBoardChannelMutations(boardId);
  const confirm = useConfirm();

  const [connectionId, setConnectionId] = useState("");
  const [channelId, setChannelId] = useState("");
  const [channelName, setChannelName] = useState("");
  const [useThreads, setUseThreads] = useState(true);
  const [tagMode, setTagMode] = useState<CommTagMode>("assignee");
  const [enabled, setEnabled] = useState(true);
  const [allowlist, setAllowlist] = useState<string[]>([]);

  const channel = data.data?.channel ?? null;
  const connections = data.data?.available_connections ?? [];

  useEffect(() => {
    if (!open || !data.data) return;
    const c = data.data.channel;
    setConnectionId(c?.connection_id ?? data.data.available_connections[0]?.id ?? "");
    setChannelId(c?.channel_id ?? "");
    setChannelName(c?.channel_name ?? "");
    setUseThreads(c?.use_threads ?? true);
    setTagMode((c?.tag_mode as CommTagMode) ?? "assignee");
    setEnabled(c?.enabled ?? true);
    setAllowlist(c?.event_allowlist ?? []);
  }, [open, data.data]);

  const connectionOptions = useMemo(
    () => connections.map((c) => ({ value: c.id, label: `${c.name} (${c.provider})` })),
    [connections],
  );
  const selectedProvider = connections.find((c) => c.id === connectionId)?.provider;
  const descriptor = (providers.data ?? []).find((d) => d.id === selectedProvider);
  const channelLabel = descriptor?.channel_id_label ?? "Channel ID";
  const channelPlaceholder = descriptor?.channel_id_placeholder ?? "Channel ID";
  const channelHelp = descriptor?.channel_id_help ?? "";
  const events = eventTypes.data?.event_types ?? [];
  const allEvents = allowlist.length === 0;

  const toggleEvent = (ev: string) =>
    setAllowlist((cur) =>
      cur.includes(ev) ? cur.filter((e) => e !== ev) : [...cur, ev],
    );

  const doSave = async () => {
    if (!connectionId) {
      toast.error("Pick a connection");
      return;
    }
    if (!channelId.trim()) {
      toast.error("Channel ID is required");
      return;
    }
    try {
      await save.mutateAsync({
        connection_id: connectionId,
        channel_id: channelId.trim(),
        channel_name: channelName.trim() || null,
        use_threads: useThreads,
        event_allowlist: allowlist,
        tag_mode: tagMode,
        enabled,
      });
      toast.success("Channel saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save channel");
    }
  };

  const doTest = async () => {
    try {
      const res = await test.mutateAsync();
      if (res.ok) toast.success("Test message sent");
      else toast.error(res.error || "Test send failed");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Test send failed");
    }
  };

  const doRemove = async () => {
    const ok = await confirm({
      title: "Remove channel?",
      description: "This board will stop sending Mattermost notifications.",
      tone: "danger",
      confirmLabel: "Remove",
    });
    if (!ok) return;
    try {
      await remove.mutateAsync();
      toast.success("Channel removed");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove channel");
    }
  };

  const busy = save.isPending || remove.isPending;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Send className="h-4 w-4" /> Channel notifications
          </DialogTitle>
        </DialogHeader>

        {data.isLoading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Spinner className="h-4 w-4" /> Loading…
          </div>
        ) : connections.length === 0 ? (
          <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-[13px] text-muted-foreground">
            No connections available. Ask an administrator to add a connection
            (Mattermost or Slack) in the Channels section first.
          </div>
        ) : (
          <div className="grid max-h-[70vh] gap-3 overflow-y-auto pr-1 pt-1">
            <Field label="Connection">
              <SelectMenu
                value={connectionId}
                onChange={setConnectionId}
                options={connectionOptions}
              />
            </Field>
            <Field label={channelLabel}>
              <Input
                value={channelId}
                onChange={(e) => setChannelId(e.target.value)}
                placeholder={channelPlaceholder}
              />
              {channelHelp && (
                <p className="pt-1 text-[12px] text-muted-foreground">{channelHelp}</p>
              )}
            </Field>
            <Field label="Channel name (optional, for display)">
              <Input
                value={channelName}
                onChange={(e) => setChannelName(e.target.value)}
                placeholder="dev-notifications"
              />
            </Field>
            <Field label="Mentions">
              <SelectMenu
                value={tagMode}
                onChange={(v) => setTagMode(v as CommTagMode)}
                options={TAG_OPTIONS}
              />
            </Field>

            <div className="grid gap-1.5">
              <span className="text-[12.5px] font-medium text-foreground">Events</span>
              <span className="text-[12px] text-muted-foreground">
                {allEvents
                  ? "Sending all notification events. Select specific ones to limit."
                  : "Only the selected events are sent."}
              </span>
              <div className="grid gap-1 pt-0.5">
                {events.map((ev) => (
                  <label key={ev} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={allowlist.includes(ev)}
                      onChange={() => toggleEvent(ev)}
                      className="h-4 w-4 accent-[var(--primary)]"
                    />
                    <span className="text-[13px] text-foreground">{eventLabel(ev)}</span>
                  </label>
                ))}
              </div>
            </div>

            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={useThreads}
                onChange={(e) => setUseThreads(e.target.checked)}
                className="h-4 w-4 accent-[var(--primary)]"
              />
              <span className="text-[13px] text-foreground">
                Group a task's updates into one thread
              </span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                className="h-4 w-4 accent-[var(--primary)]"
              />
              <span className="text-[13px] text-foreground">Notifications enabled</span>
            </label>

            {channel && (
              <div className="mt-1 grid gap-1.5 rounded-md border border-border p-3">
                <span className="text-[12.5px] font-medium text-foreground">
                  Recent deliveries
                </span>
                {(deliveries.data ?? []).length === 0 ? (
                  <p className="text-[12px] text-muted-foreground">No deliveries yet.</p>
                ) : (
                  <div className="grid gap-1">
                    {(deliveries.data ?? []).slice(0, 8).map((d) => (
                      <div
                        key={d.id}
                        className="flex items-center gap-2 text-[12px] text-muted-foreground"
                      >
                        <DeliveryBadge status={d.status} />
                        <span className="truncate">{eventLabel(d.event_type)}</span>
                        <span className="ml-auto shrink-0 tabular-nums">
                          {d.sent_at || d.created_at || ""}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <DialogFooter className="flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          {channel ? (
            <Button
              variant="ghost"
              onClick={doRemove}
              disabled={busy}
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
            >
              Remove channel
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button
              variant="secondary"
              onClick={doTest}
              disabled={!channel || test.isPending}
              title={!channel ? "Save the channel first, then send a test" : undefined}
            >
              {test.isPending ? (
                <Spinner className="h-3.5 w-3.5" />
              ) : (
                <Send className="h-3.5 w-3.5" />
              )}
              Send test
            </Button>
            <Button variant="secondary" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={doSave} disabled={busy || connections.length === 0}>
              Save
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeliveryBadge({ status }: { status: string }) {
  if (status === "sent") return <Badge variant="success">sent</Badge>;
  if (status === "failed") return <Badge variant="destructive">failed</Badge>;
  if (status === "skipped") return <Badge variant="outline">skipped</Badge>;
  return <Badge variant="secondary">{status}</Badge>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-1.5">
      <span className="text-[12.5px] font-medium text-foreground">{label}</span>
      {children}
    </label>
  );
}
