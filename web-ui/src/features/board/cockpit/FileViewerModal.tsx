import { langs } from "@uiw/codemirror-extensions-langs";
import { githubDark, githubLight } from "@uiw/codemirror-theme-github";
import CodeMirror from "@uiw/react-codemirror";
import {
  Code2,
  Download,
  Eye,
  Pencil,
  RefreshCw,
  Save,
  Trash2,
  X,
} from "@/components/icons";
import { useEffect, useMemo, useState } from "react";
import { useApi } from "@/api/ApiProvider";
import {
  useDeleteTaskFile,
  useTaskFile,
  useTaskFileBlobUrl,
  useWriteTaskFile,
} from "@/api/hooks";
import { CodeView } from "@/components/CodeView";
import { Markdown } from "@/components/Markdown";
import { useConfirm } from "@/components/ConfirmDialog";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

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

/** Map a file extension to a CodeMirror language extension (best-effort). */
const LANG_BY_EXT: Record<string, keyof typeof langs> = {
  ts: "ts",
  tsx: "tsx",
  js: "js",
  jsx: "jsx",
  mjs: "js",
  cjs: "js",
  py: "python",
  rb: "rb",
  go: "go",
  rs: "rs",
  java: "java",
  kt: "kt",
  c: "c",
  h: "c",
  cpp: "cpp",
  cc: "cpp",
  hpp: "cpp",
  cs: "cs",
  php: "php",
  swift: "swift",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  xml: "xml",
  html: "html",
  css: "css",
  scss: "scss",
  sql: "sql",
  sh: "bash",
  bash: "bash",
  md: "markdown",
  markdown: "markdown",
};

function extOf(path: string): string {
  const name = path.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

function isImagePath(path: string): boolean {
  return IMAGE_EXT.has(extOf(path));
}

function isMarkdownPath(path: string): boolean {
  const e = extOf(path);
  return e === "md" || e === "markdown";
}

/**
 * Large modal that previews a single task-workspace file. Images render
 * inline; text files render with syntax highlight and can be edited inline
 * (CodeMirror) when `canEdit`; other binaries offer a download. Reused by the
 * Artifacts tree and by note attachments.
 */
export function FileViewerModal({
  taskId,
  path,
  canEdit = false,
  onClose,
  onDeleted,
}: {
  taskId: string;
  path: string;
  canEdit?: boolean;
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const name = path.split("/").pop() ?? path;
  const isImage = isImagePath(path);
  // Note attachments live under `_notes/`; they're owned by the comment, so
  // edit/delete from the file viewer is disabled (delete the note instead).
  const effectiveCanEdit = canEdit && !path.replace(/^\/+/, "").startsWith("_notes/");

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        hideClose
        aria-describedby={undefined}
        className="flex h-[82vh] w-[min(96vw,72rem)] max-w-none flex-col gap-0 overflow-hidden p-0"
      >
        {isImage ? (
          <ImageViewer
            taskId={taskId}
            path={path}
            name={name}
            canEdit={effectiveCanEdit}
            onClose={onClose}
            onDeleted={onDeleted}
          />
        ) : (
          <TextViewer
            taskId={taskId}
            path={path}
            name={name}
            canEdit={effectiveCanEdit}
            onClose={onClose}
            onDeleted={onDeleted}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function ViewerHeader({
  name,
  path,
  children,
  onClose,
}: {
  name: string;
  path: string;
  children?: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-slate-50 px-4 py-2.5 dark:border-border dark:bg-surface-2">
      <DialogTitle className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-800 dark:text-foreground">
        <span title={path}>{name}</span>
      </DialogTitle>
      <div className="flex shrink-0 items-center gap-1">
        {children}
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
  );
}

function IconBtn({
  onClick,
  title,
  disabled,
  tone = "default",
  children,
}: {
  onClick: () => void;
  title: string;
  disabled?: boolean;
  tone?: "default" | "danger" | "brand";
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      className={cn(
        "rounded-md p-1.5 transition-colors disabled:opacity-40",
        tone === "danger"
          ? "text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10"
          : tone === "brand"
            ? "text-brand-600 hover:bg-brand-50 dark:text-brand-400 dark:hover:bg-brand-500/10"
            : "text-slate-400 hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-surface-3",
      )}
    >
      {children}
    </button>
  );
}

function useDeleteHandler(
  taskId: string,
  path: string,
  name: string,
  onClose: () => void,
  onDeleted?: () => void,
) {
  const confirm = useConfirm();
  const del = useDeleteTaskFile(taskId);
  return async () => {
    const ok = await confirm({
      title: "Delete file?",
      description: `“${name}” will be permanently removed from the workspace.`,
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!ok) return;
    await del.mutateAsync(path);
    onDeleted?.();
    onClose();
  };
}

function ImageViewer({
  taskId,
  path,
  name,
  canEdit,
  onClose,
  onDeleted,
}: {
  taskId: string;
  path: string;
  name: string;
  canEdit: boolean;
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const blob = useTaskFileBlobUrl(taskId, path);
  const handleDelete = useDeleteHandler(taskId, path, name, onClose, onDeleted);

  return (
    <>
      <ViewerHeader name={name} path={path} onClose={onClose}>
        {blob.data && (
          <a
            href={blob.data}
            download={name}
            title="Download"
            aria-label="Download"
            className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-surface-3"
          >
            <Download className="h-4 w-4" />
          </a>
        )}
        {canEdit && (
          <IconBtn onClick={handleDelete} title="Delete" tone="danger">
            <Trash2 className="h-4 w-4" />
          </IconBtn>
        )}
      </ViewerHeader>
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-[repeating-conic-gradient(#f1f5f9_0_25%,transparent_0_50%)] bg-[length:20px_20px] p-4 scrollbar-thin dark:bg-surface-1">
        {blob.isLoading ? (
          <Spinner className="h-6 w-6 text-slate-400" />
        ) : blob.isError ? (
          <p className="text-sm text-rose-500">failed to load image</p>
        ) : blob.data ? (
          <img
            src={blob.data}
            alt={name}
            className="max-h-full max-w-full rounded-lg object-contain shadow-overlay"
          />
        ) : null}
      </div>
    </>
  );
}

function TextViewer({
  taskId,
  path,
  name,
  canEdit,
  onClose,
  onDeleted,
}: {
  taskId: string;
  path: string;
  name: string;
  canEdit: boolean;
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const { client } = useApi();
  const { data, isLoading, isError, error, refetch, isFetching } = useTaskFile(
    taskId,
    path,
  );
  const write = useWriteTaskFile(taskId);
  const handleDelete = useDeleteHandler(taskId, path, name, onClose, onDeleted);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const isMarkdown = isMarkdownPath(path);
  // For markdown, default to the rendered preview; toggle to raw source.
  const [preview, setPreview] = useState(isMarkdown);

  useEffect(() => {
    if (data) setDraft(data.content);
  }, [data]);

  const isBinary =
    isError && (error as { status?: number })?.status === 409;
  const dark =
    typeof document !== "undefined" &&
    document.documentElement.classList.contains("dark");
  const langExt = useMemo(() => {
    const key = LANG_BY_EXT[extOf(path)];
    return key ? [langs[key]()] : [];
  }, [path]);

  const save = async () => {
    await write.mutateAsync({ path, content: draft });
    setEditing(false);
  };

  return (
    <>
      <ViewerHeader name={name} path={path} onClose={onClose}>
        {data && !editing && (
          <span className="mr-1 hidden text-[11px] text-slate-400 sm:inline">
            {formatBytes(data.size)}
            {data.truncated ? " · truncated" : ""}
          </span>
        )}
        {editing ? (
          <>
            <button
              type="button"
              onClick={save}
              disabled={write.isPending}
              className="inline-flex items-center gap-1 rounded-md bg-brand-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-brand-700 disabled:opacity-50"
            >
              {write.isPending ? (
                <Spinner className="h-3 w-3" />
              ) : (
                <Save className="h-3 w-3" />
              )}
              Save
            </button>
            <button
              type="button"
              onClick={() => {
                setDraft(data?.content ?? "");
                setEditing(false);
              }}
              className="rounded-md px-2.5 py-1 text-xs font-medium text-slate-500 transition-colors hover:bg-slate-200 dark:hover:bg-surface-3"
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            {data && !isBinary && isMarkdown && (
              <div className="mr-1 inline-flex rounded-md border border-slate-200 bg-slate-100 p-0.5 dark:border-border dark:bg-surface-3">
                <button
                  type="button"
                  onClick={() => setPreview(true)}
                  title="Preview"
                  aria-label="Preview"
                  className={cn(
                    "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
                    preview
                      ? "bg-card text-brand-700 shadow-raised dark:bg-surface-1 dark:text-brand-300"
                      : "text-slate-500 hover:text-slate-700 dark:text-muted-foreground",
                  )}
                >
                  <Eye className="h-3.5 w-3.5" /> Preview
                </button>
                <button
                  type="button"
                  onClick={() => setPreview(false)}
                  title="Source"
                  aria-label="Source"
                  className={cn(
                    "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
                    !preview
                      ? "bg-card text-brand-700 shadow-raised dark:bg-surface-1 dark:text-brand-300"
                      : "text-slate-500 hover:text-slate-700 dark:text-muted-foreground",
                  )}
                >
                  <Code2 className="h-3.5 w-3.5" /> Source
                </button>
              </div>
            )}
            <IconBtn onClick={() => void refetch()} title="Refresh">
              <RefreshCw className={cn("h-4 w-4", isFetching && "animate-spin")} />
            </IconBtn>
            {data && !data.truncated && canEdit && !isBinary && (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="inline-flex items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700 transition-colors hover:bg-brand-100 dark:border-brand-500/30 dark:bg-brand-500/10 dark:text-brand-300"
              >
                <Pencil className="h-3.5 w-3.5" /> Edit
              </button>
            )}
            <a
              href={client.taskWorkspaceFileRawUrl(taskId, path)}
              target="_blank"
              rel="noopener noreferrer"
              title="Download"
              aria-label="Download"
              className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-surface-3"
            >
              <Download className="h-4 w-4" />
            </a>
            {canEdit && (
              <IconBtn onClick={handleDelete} title="Delete" tone="danger">
                <Trash2 className="h-4 w-4" />
              </IconBtn>
            )}
          </>
        )}
      </ViewerHeader>
      <div className="min-h-0 flex-1 overflow-auto scrollbar-thin">
        {isLoading ? (
          <div className="flex items-center gap-2 p-4 text-sm text-slate-400">
            <Spinner className="h-4 w-4" /> loading…
          </div>
        ) : isBinary ? (
          <BinaryFallback taskId={taskId} path={path} name={name} />
        ) : isError ? (
          <p className="p-4 text-sm text-rose-500">
            {(error as Error)?.message ?? "failed to read file"}
          </p>
        ) : editing ? (
          <CodeMirror
            value={draft}
            onChange={setDraft}
            theme={dark ? githubDark : githubLight}
            extensions={langExt}
            height="100%"
            className="h-full text-[13px]"
          />
        ) : data ? (
          isMarkdown && preview ? (
            <div className="prose-chat mx-auto max-w-3xl px-5 py-4">
              <Markdown>{data.content}</Markdown>
            </div>
          ) : (
            <div className="p-3">
              <CodeView content={data.content} path={path} />
            </div>
          )
        ) : null}
      </div>
    </>
  );
}

/** Non-image binary: can't show as text, offer a download instead. */
function BinaryFallback({
  taskId,
  path,
  name,
}: {
  taskId: string;
  path: string;
  name: string;
}) {
  const blob = useTaskFileBlobUrl(taskId, path);
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <p className="text-sm text-slate-500 dark:text-muted-foreground">
        This is a binary file and can't be previewed as text.
      </p>
      {blob.data ? (
        <a
          href={blob.data}
          download={name}
          className="inline-flex items-center gap-1.5 rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-brand-700"
        >
          <Download className="h-4 w-4" /> Download {name}
        </a>
      ) : (
        <Spinner className="h-5 w-5 text-slate-400" />
      )}
    </div>
  );
}
