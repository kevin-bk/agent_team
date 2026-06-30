import { FileImage, FileText, X } from "@/components/icons";
import { cn } from "@/lib/utils";
import { FileContentViewer } from "./FileContentViewer";

const IMAGE_EXT = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "svg",
  "bmp",
  "ico",
  "avif",
]);

function isImagePath(path: string): boolean {
  const name = path.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot >= 0 && IMAGE_EXT.has(name.slice(dot + 1).toLowerCase());
}

/**
 * VS Code–style tab strip over a {@link FileContentViewer}. Open files persist
 * as tabs so the user can flip between several files without re-opening them —
 * the non-blocking replacement for the old single-file modal.
 */
export function FileTabsPane({
  taskId,
  openPaths,
  activePath,
  onActivate,
  onClose,
}: {
  taskId: string;
  openPaths: string[];
  activePath: string | null;
  onActivate: (path: string) => void;
  onClose: (path: string) => void;
}) {
  if (openPaths.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center">
        <span className="flex h-12 w-12 items-center justify-center rounded-lg bg-surface-2 text-muted-foreground">
          <FileText className="h-6 w-6" />
        </span>
        <p className="max-w-xs text-sm text-muted-foreground">
          Select a file from the tree (or a quick-access pill) to open it here.
        </p>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-stretch overflow-x-auto border-b border-border bg-surface-2 scrollbar-thin">
        {openPaths.map((path) => {
          const name = path.split("/").pop() ?? path;
          const active = path === activePath;
          const Icon = isImagePath(path) ? FileImage : FileText;
          return (
            <div
              key={path}
              className={cn(
                "group/tab flex shrink-0 items-center gap-1.5 border-r border-border px-3 py-1.5 text-[12px]",
                active
                  ? "bg-surface-1 text-foreground"
                  : "text-muted-foreground hover:bg-surface-3 hover:text-foreground",
              )}
            >
              <button
                type="button"
                onClick={() => onActivate(path)}
                title={path}
                className="flex min-w-0 max-w-[14rem] items-center gap-1.5"
              >
                <Icon className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate font-mono">{name}</span>
              </button>
              <button
                type="button"
                onClick={() => onClose(path)}
                aria-label={`Close ${name}`}
                title={`Close ${name}`}
                className="-mr-1 rounded p-0.5 opacity-0 transition-opacity hover:bg-surface-3 group-hover/tab:opacity-100"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          );
        })}
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        {activePath ? (
          // `key` forces a fresh viewer per file so editor/preview state resets.
          <FileContentViewer key={activePath} taskId={taskId} path={activePath} />
        ) : null}
      </div>
    </div>
  );
}
