import {
  ChevronRight,
  Download,
  File as FileIcon,
  Folder,
  RefreshCw,
  X,
} from "@/components/icons";
import { useState } from "react";
import { useApi } from "@/api/ApiProvider";
import { useWorkspaceFile, useWorkspaceTree } from "@/api/hooks";
import type { WorkspaceFileNode } from "@/api/types";
import { CodeView } from "@/components/CodeView";
import { Spinner } from "@/components/ui/spinner";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Right-side read-only viewer for the conversation's sandbox workspace. */
export function FileViewer({
  convId,
  initialPath,
  onClose,
}: {
  convId: string;
  initialPath: string;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(initialPath);

  return (
    <div className="flex h-full w-[32rem] shrink-0 flex-col border-l border-border bg-card/40">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="text-xs font-semibold text-muted-foreground">
          Workspace files
        </span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded p-1 text-muted-foreground hover:text-foreground"
          aria-label="Close file viewer"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="w-48 shrink-0 overflow-auto border-r border-border p-2 scrollbar-thin">
          <FileTree
            convId={convId}
            path=""
            selected={selected}
            onSelect={setSelected}
          />
        </div>
        <div className="min-w-0 flex-1 overflow-auto scrollbar-thin">
          {selected ? (
            <FileContent convId={convId} path={selected} />
          ) : (
            <div className="flex h-full items-center justify-center p-4 text-center text-xs text-muted-foreground">
              Select a file to view.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FileTree({
  convId,
  path,
  selected,
  onSelect,
}: {
  convId: string;
  path: string;
  selected: string;
  onSelect: (p: string) => void;
}) {
  const { data, isLoading, isError } = useWorkspaceTree(convId, path);
  if (isLoading)
    return (
      <div className="flex items-center gap-1.5 px-1 py-1 text-xs text-muted-foreground">
        <Spinner className="h-3 w-3" /> loading…
      </div>
    );
  if (isError)
    return <div className="px-1 py-1 text-xs text-destructive">failed to list</div>;
  const entries = data?.entries ?? [];
  if (entries.length === 0)
    return <div className="px-1 py-1 text-xs text-muted-foreground">empty</div>;
  return (
    <ul className="flex flex-col">
      {entries.map((node) => (
        <TreeNode
          key={node.path}
          convId={convId}
          node={node}
          selected={selected}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}

function TreeNode({
  convId,
  node,
  selected,
  onSelect,
}: {
  convId: string;
  node: WorkspaceFileNode;
  selected: string;
  onSelect: (p: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (node.kind === "dir") {
    return (
      <li>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <ChevronRight
            className={cn("h-3 w-3 shrink-0 transition-transform", open && "rotate-90")}
          />
          <Folder className="h-3 w-3 shrink-0 text-sky-400" />
          <span className="truncate">{node.name}</span>
        </button>
        {open && (
          <div className="ml-3 border-l border-border pl-1">
            <FileTree
              convId={convId}
              path={node.path}
              selected={selected}
              onSelect={onSelect}
            />
          </div>
        )}
      </li>
    );
  }
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(node.path)}
        className={cn(
          "flex w-full items-center gap-1 rounded px-1 py-0.5 text-left text-xs hover:bg-accent",
          selected === node.path
            ? "bg-accent text-foreground"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <FileIcon className="ml-4 h-3 w-3 shrink-0" />
        <span className="truncate">{node.name}</span>
      </button>
    </li>
  );
}

function FileContent({ convId, path }: { convId: string; path: string }) {
  const { client } = useApi();
  const { data, isLoading, isError, error, refetch, isFetching } =
    useWorkspaceFile(convId, path);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-1.5">
        <span className="truncate font-mono text-xs text-muted-foreground" title={path}>
          {path}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {data && (
            <span className="text-[10px] text-muted-foreground">
              {formatBytes(data.size)}
              {data.truncated ? " · truncated" : ""}
            </span>
          )}
          <button
            type="button"
            onClick={() => void refetch()}
            className="rounded p-1 text-muted-foreground hover:text-foreground"
            aria-label="Refresh file"
          >
            <RefreshCw className={cn("h-3 w-3", isFetching && "animate-spin")} />
          </button>
          <a
            href={client.workspaceFileRawUrl(convId, path)}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded p-1 text-muted-foreground hover:text-foreground"
            aria-label="Download file"
          >
            <Download className="h-3 w-3" />
          </a>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2 scrollbar-thin">
        {isLoading ? (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Spinner className="h-3 w-3" /> loading…
          </div>
        ) : isError ? (
          <div className="text-xs text-destructive">
            {(error as Error)?.message ?? "failed to read file"}
            {" — "}
            <a
              href={client.workspaceFileRawUrl(convId, path)}
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
            >
              open raw
            </a>
          </div>
        ) : data ? (
          <CodeView content={data.content} path={path} />
        ) : null}
      </div>
    </div>
  );
}
