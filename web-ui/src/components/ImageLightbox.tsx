import { Download, X } from "@/components/icons";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

/**
 * Full-screen image preview modal. Mirrors the image viewer used by
 * {@link FileViewerModal} (checkered backdrop, contained image, download +
 * close) so inline chat images open the same way note attachments do, instead
 * of navigating to a new browser tab. `src` is an already-resolved object/data
 * URL — the caller owns its lifecycle.
 */
export function ImageLightbox({
  src,
  name,
  onClose,
}: {
  src: string;
  name: string;
  onClose: () => void;
}) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        hideClose
        aria-describedby={undefined}
        className="flex h-[82vh] w-[min(96vw,72rem)] max-w-none flex-col gap-0 overflow-hidden p-0"
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-slate-50 px-4 py-2.5 dark:border-border dark:bg-surface-2">
          <DialogTitle className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-800 dark:text-foreground">
            <span title={name}>{name}</span>
          </DialogTitle>
          <div className="flex shrink-0 items-center gap-1">
            <a
              href={src}
              download={name}
              title="Download"
              aria-label="Download"
              className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-surface-3"
            >
              <Download className="h-4 w-4" />
            </a>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-surface-3"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-[repeating-conic-gradient(#f1f5f9_0_25%,transparent_0_50%)] bg-[length:20px_20px] p-4 scrollbar-thin dark:bg-surface-1">
          <img
            src={src}
            alt={name}
            className="max-h-full max-w-full rounded-lg object-contain shadow-overlay"
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
