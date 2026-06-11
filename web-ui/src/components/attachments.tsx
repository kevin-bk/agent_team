import { FileText, Loader2, X } from "@/components/icons";
import { useCallback, useRef, useState } from "react";
import type { UserAttachment } from "@/features/chat/types";
import { cn } from "@/lib/utils";

/** A file staged in the composer: optimistic preview + upload status. */
export interface PendingAttachment {
  localId: string;
  name: string;
  mime: string;
  size: number;
  isImage: boolean;
  previewUrl?: string;
  status: "uploading" | "done" | "error";
  serverId?: string;
  /** Raw server DTO for callers that need more than the id (e.g. note `path`). */
  serverData?: unknown;
}

let _seq = 0;
const localId = () => `pa-${Date.now().toString(36)}-${_seq++}`;

/**
 * Composer-side attachment manager: keeps an optimistic list, uploads files
 * (returning server ids), and exposes the {@link UserAttachment} list to echo
 * into the message bubble on send. Shared by the chat + task cockpit composers.
 */
export function usePendingAttachments<T extends { id: string }>(
  upload: (files: File[]) => Promise<T[]>,
  /** Best-effort server cleanup when a *staged* (unsent) item is removed. */
  deleteServer?: (item: T) => Promise<unknown> | void,
) {
  const [items, setItems] = useState<PendingAttachment[]>([]);
  // Mirror for `remove` so it can read the current item without listing `items`
  // in deps (and without running side effects inside a state updater).
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const addFiles = useCallback(
    async (files: File[]) => {
      const accepted = files.filter((f) => f.size <= 30 * 1024 * 1024);
      const tooBig = files.length - accepted.length;
      if (tooBig > 0) {
        const { toast } = await import("sonner");
        toast.error(`${tooBig} file(s) exceed the 30 MB limit`);
      }
      if (!accepted.length) return;

      const staged: PendingAttachment[] = accepted.map((f) => {
        const isImage = f.type.startsWith("image/");
        return {
          localId: localId(),
          name: f.name,
          mime: f.type || "application/octet-stream",
          size: f.size,
          isImage,
          previewUrl: isImage ? URL.createObjectURL(f) : undefined,
          status: "uploading",
        };
      });
      setItems((prev) => [...prev, ...staged]);

      try {
        const dtos = await upload(accepted);
        setItems((prev) =>
          prev.map((it) => {
            const idx = staged.findIndex((s) => s.localId === it.localId);
            if (idx < 0) return it;
            const dto = dtos[idx];
            return dto
              ? { ...it, status: "done", serverId: dto.id, serverData: dto }
              : { ...it, status: "error" };
          }),
        );
      } catch {
        const ids = new Set(staged.map((s) => s.localId));
        setItems((prev) =>
          prev.map((it) =>
            ids.has(it.localId) ? { ...it, status: "error" } : it,
          ),
        );
        const { toast } = await import("sonner");
        toast.error("Upload failed");
      }
    },
    [upload],
  );

  const remove = useCallback(
    (id: string) => {
      const hit = itemsRef.current.find((p) => p.localId === id);
      if (hit?.previewUrl) URL.revokeObjectURL(hit.previewUrl);
      // The file was already uploaded but never sent — delete it server-side
      // so the sandbox doesn't accrue orphans. Fire-and-forget.
      if (hit?.status === "done" && hit.serverData && deleteServer) {
        try {
          void deleteServer(hit.serverData as T);
        } catch {
          /* best-effort */
        }
      }
      setItems((prev) => prev.filter((p) => p.localId !== id));
    },
    [deleteServer],
  );

  // After a successful send we hand the object URLs to the message bubble, so
  // we must NOT revoke them here (that would break the just-sent image
  // preview). They are released on navigation / reload. `remove` still revokes
  // because that file never made it into a message.
  const clear = useCallback(() => {
    setItems([]);
  }, []);

  // Cancel path: nothing was sent, so every staged upload is an orphan —
  // delete each one server-side (best-effort) and release the previews.
  const discard = useCallback(() => {
    for (const it of itemsRef.current) {
      if (it.previewUrl) URL.revokeObjectURL(it.previewUrl);
      if (it.status === "done" && it.serverData && deleteServer) {
        try {
          void deleteServer(it.serverData as T);
        } catch {
          /* best-effort */
        }
      }
    }
    setItems([]);
  }, [deleteServer]);

  const toUserAttachments = useCallback((): UserAttachment[] => {
    return items
      .filter((i) => i.status === "done" && i.serverId)
      .map((i) => ({
        id: i.serverId as string,
        kind: i.isImage ? "image" : "file",
        name: i.name,
        mime: i.mime,
        url: i.previewUrl,
        size: i.size,
      }));
  }, [items]);

  /** Raw server DTOs for done uploads (callers needing the full payload). */
  const serverItems = useCallback(
    (): T[] =>
      items
        .filter((i) => i.status === "done" && i.serverData)
        .map((i) => i.serverData as T),
    [items],
  );

  const uploading = items.some((i) => i.status === "uploading");
  const hasReady = items.some((i) => i.status === "done");

  return {
    items,
    addFiles,
    remove,
    clear,
    discard,
    toUserAttachments,
    serverItems,
    uploading,
    hasReady,
  };
}

/** Thumbnail for a staged attachment: image preview with icon fallback. */
function ChipThumb({
  isImage,
  previewUrl,
  name,
}: {
  isImage: boolean;
  previewUrl?: string;
  name: string;
}) {
  const [failed, setFailed] = useState(false);
  if (isImage && previewUrl && !failed) {
    return (
      <img
        src={previewUrl}
        alt={name}
        onError={() => setFailed(true)}
        className="h-8 w-8 rounded object-cover"
      />
    );
  }
  return (
    <span className="flex h-8 w-8 items-center justify-center rounded bg-brand-100 text-brand-600 dark:bg-brand-500/20 dark:text-brand-300">
      <FileText className="h-4 w-4" />
    </span>
  );
}

/** Strip of chips shown above the composer input for staged attachments. */
export function AttachmentChips({
  items,
  onRemove,
  className,
}: {
  items: PendingAttachment[];
  onRemove: (localId: string) => void;
  className?: string;
}) {
  if (!items.length) return null;
  return (
    <div className={cn("flex flex-wrap gap-2 px-1 pb-2", className)}>
      {items.map((it) => (
        <div
          key={it.localId}
          className={cn(
            "group/att relative flex items-center gap-2 rounded-md border bg-card py-1 pl-1 pr-6 text-xs shadow-raised dark:bg-surface-2",
            it.status === "error"
              ? "border-rose-300 dark:border-rose-500/40"
              : "border-slate-200 dark:border-border",
          )}
        >
          <ChipThumb
            isImage={it.isImage}
            previewUrl={it.previewUrl}
            name={it.name}
          />
          <span className="max-w-[140px] truncate font-medium text-slate-700 dark:text-foreground">
            {it.name}
          </span>
          {it.status === "uploading" && (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />
          )}
          {it.status === "error" && (
            <span className="text-rose-500">failed</span>
          )}
          <button
            type="button"
            aria-label={`Remove ${it.name}`}
            onClick={() => onRemove(it.localId)}
            className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-surface-3"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
