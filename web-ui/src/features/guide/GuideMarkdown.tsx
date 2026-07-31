import {
  Children,
  isValidElement,
  memo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import { Link } from "react-router-dom";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { Check, Copy, ExternalLink } from "@/components/icons";
import { cn } from "@/lib/utils";
import {
  headingId,
  resolveGuideHref,
  resolveGuideImage,
} from "./guideContent";
import { MermaidDiagram } from "./MermaidDiagram";

function textFromChildren(children: ReactNode): string {
  return Children.toArray(children)
    .map((child) => {
      if (typeof child === "string" || typeof child === "number") return String(child);
      if (isValidElement<{ children?: ReactNode }>(child)) {
        return textFromChildren(child.props.children);
      }
      return "";
    })
    .join("");
}

function GuideHeading({
  level,
  children,
}: {
  level: 2 | 3;
  children?: ReactNode;
}) {
  const id = headingId(textFromChildren(children));
  const classes = level === 2 ? "guide-heading-2" : "guide-heading-3";
  const content = (
    <>
      {children}
      <a className="guide-heading-anchor" href={`#${id}`} aria-label="Liên kết tới mục này">
        #
      </a>
    </>
  );
  return level === 2
    ? <h2 id={id} className={classes}>{content}</h2>
    : <h3 id={id} className={classes}>{content}</h3>;
}

function CodeBlock({ children, className, ...props }: ComponentPropsWithoutRef<"pre">) {
  const onlyChild = Children.count(children) === 1 ? Children.only(children) : null;
  if (
    isValidElement<{ className?: string; children?: ReactNode }>(onlyChild) &&
    onlyChild.props.className?.includes("language-mermaid")
  ) {
    return <MermaidDiagram source={textFromChildren(onlyChild.props.children).trim()} />;
  }

  return <CopyablePre className={className} {...props}>{children}</CopyablePre>;
}

function CopyablePre({
  children,
  className,
  ...props
}: ComponentPropsWithoutRef<"pre">) {
  const ref = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  const copy = () => {
    const value = ref.current?.innerText;
    if (!value) return;
    void navigator.clipboard?.writeText(value).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    });
  };
  return (
    <div className="group/code relative">
      <button
        type="button"
        onClick={copy}
        className={cn(
          "absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded border border-white/15",
          "bg-slate-900/80 px-2 py-1 text-[11px] text-slate-200 opacity-0 backdrop-blur",
          "transition-opacity hover:text-white group-hover/code:opacity-100 focus:opacity-100",
        )}
        aria-label={copied ? "Đã sao chép" : "Sao chép code"}
      >
        {copied
          ? <Check className="h-3.5 w-3.5 text-emerald-300" />
          : <Copy className="h-3.5 w-3.5" />}
        {copied ? "Đã chép" : "Sao chép"}
      </button>
      <pre ref={ref} className={className} {...props}>{children}</pre>
    </div>
  );
}

export const GuideMarkdown = memo(function GuideMarkdown({
  children,
}: {
  children: string;
}) {
  const components: Components = {
    a: ({ node: _node, href, children: linkChildren, ...props }) => {
      const internal = resolveGuideHref(href);
      if (internal) {
        return <Link to={internal} {...props}>{linkChildren}</Link>;
      }
      if (href?.startsWith("#")) {
        return <a href={href} {...props}>{linkChildren}</a>;
      }
      return (
        <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
          {linkChildren}
          <ExternalLink className="ml-1 inline-flex h-3 w-3 align-[-1px]" />
        </a>
      );
    },
    img: ({ node: _node, src, alt, ...props }) => (
      <img src={resolveGuideImage(src)} alt={alt ?? ""} loading="lazy" {...props} />
    ),
    h2: ({ node: _node, children }) => <GuideHeading level={2}>{children}</GuideHeading>,
    h3: ({ node: _node, children }) => <GuideHeading level={3}>{children}</GuideHeading>,
    pre: ({ node: _node, ...props }) => <CodeBlock {...props} />,
  };

  return (
    <div className="guide-prose">
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
