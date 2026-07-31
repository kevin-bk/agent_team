import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Clock,
  FileText,
  Search,
} from "@/components/icons";
import { cn } from "@/lib/utils";
import { GuideMarkdown } from "./GuideMarkdown";
import {
  getGuideDocument,
  getGuideNeighbors,
  guideDocuments,
  guideHeadings,
  guideReadingMinutes,
  guideSections,
  stripFirstHeading,
} from "./guideContent";

export function GuidePage() {
  const { slug } = useParams<{ slug?: string }>();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const document = getGuideDocument(slug);

  useEffect(() => {
    const main = window.document.getElementById("guide-scroll-area");
    if (!window.location.hash) main?.scrollTo({ top: 0 });
  }, [slug]);

  if (!document) return <Navigate to="/guide/start" replace />;

  const headings = guideHeadings(document.content);
  const neighbors = getGuideNeighbors(document.slug);
  const minutes = guideReadingMinutes(document.content);
  const isLanding = document.slug === "start";
  const normalizedQuery = query.trim().toLocaleLowerCase("vi");
  const visibleDocuments = normalizedQuery
    ? guideDocuments.filter((item) =>
        `${item.title} ${item.summary}`.toLocaleLowerCase("vi").includes(normalizedQuery),
      )
    : guideDocuments;

  return (
    <div className="flex h-full min-h-0 bg-background">
      <aside className="hidden w-[278px] shrink-0 flex-col border-r border-border bg-surface-1 lg:flex">
        <div className="border-b border-border px-4 py-4">
          <Link
            to="/guide/start"
            className="flex items-center gap-2 text-[15px] font-semibold text-foreground"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded bg-primary text-primary-foreground">
              <FileText className="h-[18px] w-[18px]" />
            </span>
            Agent Team Guide
          </Link>
          <label className="relative mt-3 block">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <span className="sr-only">Tìm trong hướng dẫn</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Tìm trong hướng dẫn…"
              className="h-9 w-full rounded border border-input bg-background pl-8 pr-3 text-[13px] outline-none transition focus:border-primary focus:ring-1 focus:ring-ring"
            />
          </label>
        </div>
        <nav className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-3 py-3">
          {guideSections.map((section) => {
            const sectionDocuments = visibleDocuments.filter(
              (item) => item.section === section,
            );
            if (sectionDocuments.length === 0) return null;
            return (
              <div key={section} className="mb-4">
                <div className="mb-1 px-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                  {section}
                </div>
                {sectionDocuments.map((item) => (
                  <Link
                    key={item.slug}
                    to={`/guide/${item.slug}`}
                    title={item.summary}
                    className={cn(
                      "mb-0.5 flex min-h-8 items-center rounded px-2 py-1.5 text-[13px] leading-4 transition-colors",
                      item.slug === document.slug
                        ? "bg-primary/10 font-medium text-primary"
                        : "text-foreground hover:bg-surface-3",
                    )}
                  >
                    {item.title}
                  </Link>
                ))}
              </div>
            );
          })}
          {visibleDocuments.length === 0 && (
            <p className="px-2 py-5 text-center text-xs text-muted-foreground">
              Không tìm thấy nội dung phù hợp.
            </p>
          )}
        </nav>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-background px-4 lg:hidden">
          <label className="text-xs font-medium text-muted-foreground" htmlFor="guide-page-select">
            Chương
          </label>
          <select
            id="guide-page-select"
            value={document.slug}
            onChange={(event) => navigate(`/guide/${event.target.value}`)}
            className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1.5 text-sm text-foreground"
          >
            {guideSections.map((section) => (
              <optgroup key={section} label={section}>
                {guideDocuments
                  .filter((item) => item.section === section)
                  .map((item) => (
                    <option key={item.slug} value={item.slug}>{item.title}</option>
                  ))}
              </optgroup>
            ))}
          </select>
        </div>

        <div id="guide-scroll-area" className="scrollbar-thin min-h-0 flex-1 overflow-y-auto scroll-smooth">
          <div className="mx-auto flex w-full max-w-[1320px] items-start px-5 py-8 sm:px-8 lg:px-10 lg:py-10">
            <article className="min-w-0 flex-1 xl:pr-14">
              {isLanding ? (
                <GuideLanding />
              ) : (
                <header className="mb-8 border-b border-border pb-7">
                  <Link
                    to="/guide/start"
                    className="mb-4 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-primary"
                  >
                    Agent Team Guide
                    <span aria-hidden="true">/</span>
                    {document.section}
                  </Link>
                  <h1 className="max-w-3xl text-3xl font-semibold tracking-[-0.025em] text-foreground-strong sm:text-[38px] sm:leading-[1.15]">
                    {document.title}
                  </h1>
                  <p className="mt-3 max-w-2xl text-[16px] leading-7 text-muted-foreground">
                    {document.summary}
                  </p>
                  <div className="mt-4 flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Clock className="h-3.5 w-3.5" />
                    Khoảng {minutes} phút đọc
                  </div>
                </header>
              )}

              <GuideMarkdown>
                {stripFirstHeading(document.content)}
              </GuideMarkdown>

              <footer className="mt-14 grid gap-3 border-t border-border pt-6 sm:grid-cols-2">
                {neighbors.previous ? (
                  <Link
                    to={`/guide/${neighbors.previous.slug}`}
                    className="group rounded border border-border bg-surface-1 p-4 transition hover:border-primary/50 hover:bg-surface-2"
                  >
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <ArrowLeft className="h-3.5 w-3.5 transition-transform group-hover:-translate-x-0.5" />
                      Bài trước
                    </span>
                    <span className="mt-1 block font-medium text-foreground group-hover:text-primary">
                      {neighbors.previous.title}
                    </span>
                  </Link>
                ) : <span />}
                {neighbors.next && (
                  <Link
                    to={`/guide/${neighbors.next.slug}`}
                    className="group rounded border border-border bg-surface-1 p-4 text-right transition hover:border-primary/50 hover:bg-surface-2"
                  >
                    <span className="flex items-center justify-end gap-1 text-xs text-muted-foreground">
                      Bài tiếp theo
                      <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
                    </span>
                    <span className="mt-1 block font-medium text-foreground group-hover:text-primary">
                      {neighbors.next.title}
                    </span>
                  </Link>
                )}
              </footer>
            </article>

            {!isLanding && headings.length > 1 && (
              <aside className="sticky top-8 hidden w-52 shrink-0 border-l border-border pl-5 xl:block">
                <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                  Trong trang này
                </div>
                <nav>
                  {headings.map((heading) => (
                    <a
                      key={`${heading.id}-${heading.depth}`}
                      href={`#${heading.id}`}
                      className={cn(
                        "mb-2 block text-[12.5px] leading-4 text-muted-foreground transition hover:text-primary",
                        heading.depth === 3 && "pl-3",
                      )}
                    >
                      {heading.text}
                    </a>
                  ))}
                </nav>
              </aside>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function GuideLanding() {
  const paths = useMemo(() => [
    {
      label: "Tôi mới bắt đầu",
      detail: "Hiểu Agent Team, ACP, sandbox và engineering loop.",
      to: "/guide/agent-team-overview",
    },
    {
      label: "Tôi cần setup",
      detail: "Chuẩn bị Claude, Codex, skill packs và runtime.",
      to: "/guide/before-first-task",
    },
    {
      label: "Tôi muốn chạy task",
      detail: "Tạo board, duyệt plan, test và đọc evidence.",
      to: "/guide/create-first-board",
    },
  ], []);

  return (
    <header className="dot-grid relative mb-9 overflow-hidden rounded-xl border border-border bg-surface-1 px-6 py-7 sm:px-9 sm:py-9">
      <div className="relative">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="rounded bg-primary/10 px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.1em] text-primary">
            Agent Team Handbook
          </span>
          <span className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1.5 text-xs font-medium text-sky-800 shadow-sm dark:border-sky-700 dark:bg-sky-900/40 dark:text-sky-200">
            From Kevin with Love <span className="font-mono font-bold text-sky-500">&lt;3</span>
          </span>
        </div>
        <h1 className="mt-6 max-w-3xl text-3xl font-semibold tracking-[-0.03em] text-foreground-strong sm:text-[42px] sm:leading-[1.12]">
          Hiểu Agent Team từ task đầu tiên đến engineering loop có bằng chứng.
        </h1>
        <p className="mt-4 max-w-2xl text-[16px] leading-7 text-muted-foreground">
          Hướng dẫn bằng tiếng Việt cho BA, Product Owner, QA, developer và người
          vận hành — bắt đầu từ bức tranh dễ hiểu, sau đó mới đi vào cấu hình.
        </p>
        <Link
          to="/guide/agent-team-overview"
          className="mt-6 inline-flex h-10 items-center gap-2 rounded bg-primary px-4 text-sm font-semibold text-primary-foreground transition hover:brightness-110"
        >
          Bắt đầu đọc
          <ArrowRight className="h-4 w-4" />
        </Link>

        <div className="mt-8 grid gap-3 border-t border-border pt-6 md:grid-cols-3">
          {paths.map((path) => (
            <Link
              key={path.to}
              to={path.to}
              className="group rounded border border-border bg-background/90 p-4 shadow-sm transition hover:border-primary/50"
            >
              <span className="flex items-center justify-between text-sm font-semibold text-foreground group-hover:text-primary">
                {path.label}
                <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
              </span>
              <span className="mt-1.5 block text-xs leading-5 text-muted-foreground">
                {path.detail}
              </span>
            </Link>
          ))}
        </div>
      </div>
    </header>
  );
}
