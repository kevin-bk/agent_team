import { MoreVertical, Pencil, Plus, Search, Trash2 } from "@/components/icons";
import { useState } from "react";
import { toast } from "sonner";
import {
  useConversations,
  useCreateConversation,
  useDeleteConversation,
  usePatchConversation,
} from "@/api/hooks";
import type { ConversationSummary } from "@/api/types";
import { useConfirm, usePrompt } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

export function SessionList({
  profile,
  selectedId,
  onSelect,
}: {
  profile: string | undefined;
  selectedId: string | undefined;
  onSelect: (convId: string) => void;
}) {
  const { data: conversations, isLoading } = useConversations(profile);
  const create = useCreateConversation(profile);
  const patch = usePatchConversation(profile);
  const remove = useDeleteConversation(profile);
  const confirm = useConfirm();
  const prompt = usePrompt();
  const [filter, setFilter] = useState("");

  const newConversation = () => {
    if (!profile) return;
    create.mutate("New conversation", {
      onSuccess: (conv) => onSelect(conv.conv_id),
      onError: () => toast.error("Could not create conversation"),
    });
  };

  const rename = async (conv: ConversationSummary) => {
    const title = await prompt({
      title: "Rename conversation",
      defaultValue: conv.title,
      placeholder: "Conversation name",
      confirmLabel: "Rename",
    });
    if (title != null && title.trim() && title.trim() !== conv.title) {
      patch.mutate({ convId: conv.conv_id, title: title.trim() });
    }
  };

  const del = async (conv: ConversationSummary) => {
    const ok = await confirm({
      title: "Delete conversation?",
      description: (
        <>
          <span className="font-medium text-foreground">
            {conv.title || "Untitled"}
          </span>{" "}
          and its history will be removed permanently. This cannot be undone.
        </>
      ),
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (ok) {
      remove.mutate(conv.conv_id, {
        onSuccess: () => {
          if (selectedId === conv.conv_id) onSelect("");
        },
      });
    }
  };

  const items = (conversations ?? []).filter((c) =>
    c.title.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 px-3 py-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search…"
            className="h-9 pl-8"
          />
        </div>
        <Button
          size="icon"
          variant="secondary"
          onClick={newConversation}
          disabled={!profile || create.isPending}
          title="New conversation"
        >
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 scrollbar-thin">
        {isLoading && (
          <div className="flex items-center gap-2 px-2 py-3 text-sm text-muted-foreground">
            <Spinner /> loading…
          </div>
        )}
        {items.map((conv) => (
          <div
            key={conv.conv_id}
            className={cn(
              "group flex cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-sm transition-colors",
              selectedId === conv.conv_id
                ? "bg-accent text-accent-foreground"
                : "hover:bg-accent/60",
            )}
            onClick={() => onSelect(conv.conv_id)}
          >
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">
                {conv.title || "Untitled"}
              </div>
              <div className="truncate text-xs text-muted-foreground">
                {relativeTime(conv.last_run_at_ms ?? conv.updated_at_ms)} ·{" "}
                {conv.total_runs} run{conv.total_runs === 1 ? "" : "s"}
              </div>
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className="opacity-0 transition-opacity group-hover:opacity-100"
                  onClick={(e) => e.stopPropagation()}
                >
                  <MoreVertical className="h-4 w-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                <DropdownMenuItem onSelect={() => rename(conv)}>
                  <Pencil className="h-4 w-4" /> Rename
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => del(conv)}>
                  <Trash2 className="h-4 w-4 text-destructive" /> Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ))}
        {!isLoading && items.length === 0 && (
          <div className="px-2 py-3 text-sm text-muted-foreground">
            No conversations.
          </div>
        )}
      </div>
    </div>
  );
}
