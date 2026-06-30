import { Editor } from "@monaco-editor/react";
import type { editor as MonacoEditor } from "monaco-editor";
import { Code2, Download, Eye, FileImage, RefreshCw } from "@/components/icons";
import { useMemo, useState } from "react";
import { useApi } from "@/api/ApiProvider";
import { useTaskFile, useTaskFileBlobUrl } from "@/api/hooks";
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
}: {
  taskId: string;
  path: string;
}) {
  const name = path.split("/").pop() ?? path;
  const isImage = IMAGE_EXT.has(extOf(path));

  if (isImage) {
    return <ImageBody taskId={taskId} path={path} name={name} />;
  }
  return <TextBody taskId={taskId} path={path} name={name} />;
}

function Toolbar({
  name,
  size,
  truncated,
  children,
}: {
  name: string;
  size?: number;
  truncated?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-border bg-surface-2 px-3 py-1.5">
      <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-foreground">
        {name}
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
}: {
  taskId: string;
  path: string;
  name: string;
}) {
  const { client } = useApi();
  const { data, isLoading, isError, error, refetch, isFetching } = useTaskFile(
    taskId,
    path,
  );
  const dark = useIsDark();
  const theme = dark ? "agent-team-dark" : "agent-team-light";
  const language = useMemo(() => monacoLanguageFromPath(path), [path]);
  const markdown = isMarkdownPath(path);
  const [preview, setPreview] = useState(markdown);

  const isBinary = isError && (error as { status?: number })?.status === 409;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar name={name} size={data?.size} truncated={data?.truncated}>
        {data && !isBinary && markdown && (
          <div className="mr-1 inline-flex rounded-md border border-border bg-surface-1 p-0.5">
            <ToggleBtn active={preview} onClick={() => setPreview(true)}>
              <Eye className="h-3.5 w-3.5" /> Preview
            </ToggleBtn>
            <ToggleBtn active={!preview} onClick={() => setPreview(false)}>
              <Code2 className="h-3.5 w-3.5" /> Source
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
                <Markdown>{data.content}</Markdown>
              </div>
            </div>
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
