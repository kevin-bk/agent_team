import { memo, useRef, useState, type ComponentPropsWithoutRef } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import "highlight.js/styles/github-dark.css";
import { Check, Copy } from "@/components/icons";
import { useTaskFileBlobUrl } from "@/api/hooks";
import { cn } from "@/lib/utils";

const ABSOLUTE_SRC = /^(https?:|data:|blob:|\/\/)/i;

/** Fenced code block with a hover copy button (reads the rendered text). */
function CodeBlock({ children, className, ...props }: ComponentPropsWithoutRef<"pre">) {
  const ref = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  const onCopy = () => {
    const text = ref.current?.innerText ?? "";
    if (!text) return;
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <div className="group/code relative">
      <button
        type="button"
        onClick={onCopy}
        aria-label={copied ? "Copied" : "Copy code"}
        title={copied ? "Copied" : "Copy code"}
        className={cn(
          "absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded-md border border-border",
          "bg-card/80 px-1.5 py-1 text-[11px] text-muted-foreground backdrop-blur",
          "opacity-0 transition-opacity hover:text-foreground group-hover/code:opacity-100",
          "focus:opacity-100 focus:outline-none focus:ring-1 focus:ring-ring",
        )}
      >
        {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
      <pre ref={ref} className={className} {...props}>
        {children}
      </pre>
    </div>
  );
}

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
    pre: ({ node: _node, ...props }) => <CodeBlock {...props} />,
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
