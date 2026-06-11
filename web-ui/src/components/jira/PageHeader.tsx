import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Breadcrumbs, type Crumb } from "./Breadcrumbs";

/**
 * Classic-Jira page heading: breadcrumbs + a large 24px medium-weight title
 * sitting directly on the white page — no boxed bar, no border strip
 * (mirrors the jira-clone board page: breadcrumbs, then "Kanban board").
 */
export function PageHeader({
  breadcrumbs,
  title,
  titleAdornment,
  subtitle,
  actions,
  className,
}: {
  breadcrumbs?: Crumb[];
  title: ReactNode;
  /** Rendered to the right of the title (e.g. a role lozenge). */
  titleAdornment?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("flex flex-col px-8 pt-6", className)}>
      {breadcrumbs && breadcrumbs.length > 0 && (
        <Breadcrumbs items={breadcrumbs} />
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <h1 className="truncate text-2xl font-medium text-foreground">
            {title}
          </h1>
          {titleAdornment}
        </div>
        {actions && (
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {actions}
          </div>
        )}
      </div>
      {subtitle && (
        <p className="mt-1 text-[13px] text-muted-foreground">{subtitle}</p>
      )}
    </header>
  );
}
