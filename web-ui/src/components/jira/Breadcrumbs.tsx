import { Fragment } from "react";
import { ChevronRight } from "@/components/icons";
import { cn } from "@/lib/utils";

export interface Crumb {
  label: string;
  onClick?: () => void;
  href?: string;
}

/**
 * Jira breadcrumb trail (e.g. "Projects / Sprint board / Kanban board").
 * The last crumb renders as the current (non-interactive) page.
 */
export function Breadcrumbs({
  items,
  className,
}: {
  items: Crumb[];
  className?: string;
}) {
  return (
    <nav
      aria-label="Breadcrumb"
      className={cn(
        "flex items-center gap-1 text-[13px] text-muted-foreground",
        className,
      )}
    >
      {items.map((item, i) => {
        const last = i === items.length - 1;
        const content = last ? (
          <span className="font-medium text-foreground">{item.label}</span>
        ) : item.href ? (
          <a
            href={item.href}
            className="rounded px-0.5 transition-colors hover:text-primary hover:underline"
          >
            {item.label}
          </a>
        ) : (
          <button
            type="button"
            onClick={item.onClick}
            className="rounded px-0.5 transition-colors hover:text-primary hover:underline"
          >
            {item.label}
          </button>
        );
        return (
          <Fragment key={`${item.label}-${i}`}>
            {content}
            {!last && (
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-border-strong" />
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}
