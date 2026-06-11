import { memo } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import "highlight.js/styles/github-dark.css";
import { useTaskFileBlobUrl } from "@/api/hooks";

const ABSOLUTE_SRC = /^(https?:|data:|blob:|\/\/)/i;

/** Inline image whose `src` is a task-workspace path (fetched with auth). */
function WorkspaceImage({
  taskId,
  src,
  alt,
}: {
  taskId: string;
  src: string;
  alt?: string;
}) {
  const blob = useTaskFileBlobUrl(taskId, src);
  if (blob.data)
    return (
      <img src={blob.data} alt={alt ?? ""} className="max-h-96 rounded border border-border" />
    );
  if (blob.isError)
    return (
      <span className="text-xs text-rose-500">[image unavailable: {alt || src}]</span>
    );
  return (
    <span className="text-xs text-muted-foreground">loading {alt || "image"}…</span>
  );
}

/**
 * Assistant text renderer: GFM + syntax-highlighted code blocks. Pass `taskId`
 * to resolve workspace-relative image sources (e.g. Jira inline attachments)
 * via the authenticated file route.
 */
export const Markdown = memo(function Markdown({
  children,
  taskId,
}: {
  children: string;
  taskId?: string;
}) {
  const components: Components = {
    a: ({ node: _node, ...props }) => (
      <a {...props} target="_blank" rel="noopener noreferrer" />
    ),
    img: ({ node: _node, src, alt, ...props }) => {
      if (taskId && typeof src === "string" && src && !ABSOLUTE_SRC.test(src)) {
        return <WorkspaceImage taskId={taskId} src={src} alt={alt} />;
      }
      return <img src={src} alt={alt ?? ""} {...props} />;
    },
  };
  return (
    <div className="prose-chat">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={components}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});
