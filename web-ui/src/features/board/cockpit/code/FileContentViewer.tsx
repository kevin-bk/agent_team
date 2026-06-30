import { Editor } from "@monaco-editor/react";
import type { editor as MonacoEditor } from "monaco-editor";
import {
  Code2,
  Download,
  Eye,
  FileImage,
  RefreshCw,
  Save,
  X,
} from "@/components/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useApi } from "@/api/ApiProvider";
import {
  qk,
  useTaskFile,
  useTaskFileBlobUrl,
  useWriteTaskFile,
} from "@/api/hooks";
import { Markdown } from "@/components/Markdown";
import { Spinner } from "@/components/ui/spinner";
import { formatBytes } from "@/lib/format";
import "@/lib/monaco-setup";
import { monacoLanguageFromPath, isMarkdownPath } from "@/lib/monacoLanguage";
import { useIsDark } from "@/lib/useIsDark";
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

function extOf(path: string): string {
  const name = path.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/**
 * Per-path cache of unsaved editor text, owned by {@link CodeWorkspace}. Lifting
 * drafts above the (keyed, per-tab) viewer lets edits survive tab/view switches.
 */
export interface DraftStore {
  get(path: string): string | undefined;
  has(path: string): boolean;
  set(path: string, value: string): void;
  clear(path: string): void;
}

const VIEWER_OPTIONS: MonacoEditor.IEditorConstructionOptions = {
  readOnly: true,
  renderValidationDecorations: "off",
  scrollBeyondLastLine: false,
  minimap: { enabled: true, maxColumn: 80 },
  automaticLayout: true,
  fontSize: 12.5,
  lineNumbersMinChars: 3,
  folding: true,
  wordWrap: "off",
  scrollbar: { alwaysConsumeMouseWheel: true },
};

/**
 * Read-only viewer for one task-workspace file, filling its container. Code
 * renders in Monaco (syntax + folding + minimap); markdown defaults to a
 * rendered preview with a Source toggle; images render inline; other binaries
 * offer a download. Used by {@link FileTabsPane} in the Code workspace — the
 * non-blocking replacement for the old single-file modal.
 */
export function FileContentViewer({
  taskId,
  path,
  canEdit = false,
  drafts,
}: {
  taskId: string;
  path: string;
  /** Allow inline editing (Save writes the workspace file). */
  canEdit?: boolean;
  /** Shared draft cache so unsaved edits persist across tab switches. */
  drafts?: DraftStore;
}) {
  const name = path.split("/").pop() ?? path;
  const isImage = IMAGE_EXT.has(extOf(path));

  if (isImage) {
    return <ImageBody taskId={taskId} path={path} name={name} />;
  }
  return (
    <TextBody
      taskId={taskId}
      path={path}
      name={name}
      canEdit={canEdit}
      drafts={drafts}
    />
  );
}

function Toolbar({
  name,
  size,
  truncated,
  dirty,
  children,
}: {
  name: string;
  size?: number;
  truncated?: boolean;
  dirty?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-border bg-surface-2 px-3 py-1.5">
      {dirty && (
        <span
          title="Unsaved changes"
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400"
        />
      )}
      <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-foreground">
        {name}
        {dirty ? " •" : ""}
      </span>
      {size != null && (
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
          {formatBytes(size)}
          {truncated ? " · truncated" : ""}
        </span>
      )}
      <div className="flex shrink-0 items-center gap-1">{children}</div>
    </div>
  );
}

function TextBody({
  taskId,
  path,
  name,
  canEdit,
  drafts,
}: {
  taskId: string;
  path: string;
  name: string;
  canEdit: boolean;
  drafts?: DraftStore;
}) {
  const { client } = useApi();
  const qc = useQueryClient();
  const { data, isLoading, isError, error, refetch, isFetching } = useTaskFile(
    taskId,
    path,
  );
  const write = useWriteTaskFile(taskId);
  const dark = useIsDark();
  const theme = dark ? "agent-team-dark" : "agent-team-light";
  const language = useMemo(() => monacoLanguageFromPath(path), [path]);
  const markdown = isMarkdownPath(path);
  // Editable files open straight in edit/source mode; the reader can flip to
  // preview. Read-only viewers keep the rendered preview as the default.
  const [preview, setPreview] = useState(markdown && !canEdit);
  const [dirty, setDirty] = useState(false);
  // Live editor text + the saved baseline it's compared against, both in refs to
  // avoid re-rendering the editor on every keystroke.
  const draftRef = useRef<string>("");
  const baselineRef = useRef<string>("");
  const editorRef = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null);

  const isBinary = isError && (error as { status?: number })?.status === 409;
  // Truncated files only carry a prefix of the content — editing would clobber
  // the rest, so they stay read-only. Markdown is edited in Source mode.
  const editable = canEdit && !!data && !isBinary && !data.truncated;
  const editorIsEditable = editable && !(markdown && preview);
  // Initial editor text: a pending draft (carried across tab switches) wins over
  // the on-disk content. Read once per mount (the editor is uncontrolled).
  const initialText = data ? (drafts?.get(path) ?? data.content) : "";

  useEffect(() => {
    if (!data) return;
    baselineRef.current = data.content;
    const d = drafts?.get(path);
    draftRef.current = d ?? data.content;
    setDirty(d !== undefined && d !== data.content);
  }, [data, drafts, path]);

  const save = async () => {
    try {
      await write.mutateAsync({ path, content: draftRef.current });
      baselineRef.current = draftRef.current;
      drafts?.clear(path);
      setDirty(false);
      // The file changed on disk → its git diff is now stale.
      void qc.invalidateQueries({ queryKey: qk.taskChanges(taskId) });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save file");
    }
  };

  const discard = () => {
    draftRef.current = baselineRef.current;
    editorRef.current?.setValue(baselineRef.current);
    drafts?.clear(path);
    setDirty(false);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar name={name} size={data?.size} truncated={data?.truncated} dirty={dirty}>
        {dirty && (
          <>
            <button
              type="button"
              onClick={save}
              disabled={write.isPending}
              className="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground transition-colors hover:opacity-90 disabled:opacity-50"
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
              onClick={discard}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground"
            >
              <X className="h-3 w-3" /> Discard
            </button>
          </>
        )}
        {data && !isBinary && markdown && (
          <div className="mr-1 inline-flex rounded-md border border-border bg-surface-1 p-0.5">
            <ToggleBtn active={preview} onClick={() => setPreview(true)}>
              <Eye className="h-3.5 w-3.5" /> Preview
            </ToggleBtn>
            <ToggleBtn active={!preview} onClick={() => setPreview(false)}>
              <Code2 className="h-3.5 w-3.5" /> {editable ? "Edit" : "Source"}
            </ToggleBtn>
          </div>
        )}
        <button
          type="button"
          onClick={() => void refetch()}
          title="Refresh"
          aria-label="Refresh"
          className="rounded p-1 text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} />
        </button>
        <a
          href={client.taskWorkspaceFileRawUrl(taskId, path)}
          target="_blank"
          rel="noopener noreferrer"
          title="Download"
          aria-label="Download"
          className="rounded p-1 text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground"
        >
          <Download className="h-3.5 w-3.5" />
        </a>
      </Toolbar>

      <div className="min-h-0 flex-1">
        {isLoading ? (
          <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
            <Spinner className="h-4 w-4" /> loading…
          </div>
        ) : isBinary ? (
          <BinaryFallback taskId={taskId} path={path} name={name} />
        ) : isError ? (
          <p className="p-4 text-sm text-rose-500">
            {(error as Error)?.message ?? "failed to read file"}
          </p>
        ) : data ? (
          markdown && preview ? (
            <div className="h-full overflow-auto scrollbar-thin">
              <div className="prose-chat mx-auto max-w-3xl px-5 py-4">
                <Markdown>{drafts?.get(path) ?? data.content}</Markdown>
              </div>
            </div>
          ) : editorIsEditable ? (
            <Editor
              className="h-full w-full"
              language={language}
              defaultValue={initialText}
              theme={theme}
              onMount={(ed) => {
                editorRef.current = ed;
              }}
              onChange={(v) => {
                const text = v ?? "";
                draftRef.current = text;
                const isDirty = text !== baselineRef.current;
                setDirty(isDirty);
                if (isDirty) drafts?.set(path, text);
                else drafts?.clear(path);
              }}
              options={{ ...VIEWER_OPTIONS, readOnly: false }}
            />
          ) : (
            <Editor
              className="h-full w-full"
              language={language}
              value={data.content}
              theme={theme}
              options={VIEWER_OPTIONS}
            />
          )
        ) : null}
      </div>
    </div>
  );
}

function ToggleBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function ImageBody({
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
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar name={name}>
        {blob.data && (
          <a
            href={blob.data}
            download={name}
            title="Download"
            aria-label="Download"
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground"
          >
            <Download className="h-3.5 w-3.5" />
          </a>
        )}
      </Toolbar>
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-[repeating-conic-gradient(#f1f5f9_0_25%,transparent_0_50%)] bg-[length:20px_20px] p-4 scrollbar-thin dark:bg-surface-1">
        {blob.isLoading ? (
          <Spinner className="h-6 w-6 text-muted-foreground" />
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
    </div>
  );
}

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
      <span className="flex h-12 w-12 items-center justify-center rounded-lg bg-surface-3 text-muted-foreground">
        <FileImage className="h-6 w-6" />
      </span>
      <p className="text-sm text-muted-foreground">
        This is a binary file and can't be previewed as text.
      </p>
      {blob.data ? (
        <a
          href={blob.data}
          download={name}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:opacity-90"
        >
          <Download className="h-4 w-4" /> Download {name}
        </a>
      ) : (
        <Spinner className="h-5 w-5 text-muted-foreground" />
      )}
    </div>
  );
}
