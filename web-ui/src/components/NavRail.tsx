import {
  FolderGit2,
  LayoutGrid,
  MessagesSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Sun,
} from "@/components/icons";
import { useMe } from "@/api/hooks";
import { cn } from "@/lib/utils";
import { AuthButton } from "./AuthButton";
import type { View } from "./Sidebar";

interface RailItem {
  id: View;
  label: string;
  icon: typeof LayoutGrid;
}

const ITEMS: RailItem[] = [
  { id: "board", label: "Boards", icon: LayoutGrid },
  { id: "chat", label: "Chats", icon: MessagesSquare },
];

const ADMIN_ITEMS: RailItem[] = [
  { id: "repos", label: "Repositories", icon: FolderGit2 },
];

/**
 * Jira's signature far-left navigation rail: a slim, deep-blue column of
 * product-level switches. It owns the top-level view switch; the lighter
 * {@link Sidebar} beside it carries per-view context.
 */
export function NavRail({
  view,
  onViewChange,
  dark,
  onToggleTheme,
  collapsed,
  onToggleCollapse,
}: {
  view: View;
  onViewChange: (v: View) => void;
  dark: boolean;
  onToggleTheme: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}) {
  const me = useMe();
  const items = me.data?.is_admin ? [...ITEMS, ...ADMIN_ITEMS] : ITEMS;
  return (
    <aside className="flex h-full w-16 shrink-0 flex-col items-center gap-1.5 bg-nav py-4 text-nav-foreground">
      <a
        href="/"
        title="deep-agent"
        className="mb-2 flex h-9 w-9 items-center justify-center"
      >
        <img
          src={`${import.meta.env.BASE_URL}deep-agent-logo.svg`}
          alt="deep-agent"
          className="h-7 w-7 rounded-md"
        />
      </a>

      {items.map(({ id, label, icon: Icon }) => {
        const active = view === id;
        return (
          <button
            key={id}
            type="button"
            onClick={() => onViewChange(id)}
            title={label}
            aria-label={label}
            aria-current={active ? "page" : undefined}
            className={cn(
              "relative flex h-10 w-10 items-center justify-center rounded-full transition-colors duration-100",
              active
                ? "bg-white/25 text-white"
                : "text-nav-foreground/80 hover:bg-nav-hover hover:text-white",
            )}
          >
            {active && (
              <span className="absolute -left-[13px] top-1/2 h-5 w-1 -translate-y-1/2 rounded-r bg-white" />
            )}
            <Icon className="h-[20px] w-[20px]" />
          </button>
        );
      })}

      <div className="mt-auto flex flex-col items-center gap-1.5">
        <RailIconButton
          label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={onToggleCollapse}
        >
          {collapsed ? (
            <PanelLeftOpen className="h-[18px] w-[18px]" />
          ) : (
            <PanelLeftClose className="h-[18px] w-[18px]" />
          )}
        </RailIconButton>
        <RailIconButton
          label={dark ? "Switch to light" : "Switch to dark"}
          onClick={onToggleTheme}
        >
          {dark ? (
            <Sun className="h-[18px] w-[18px]" />
          ) : (
            <Moon className="h-[18px] w-[18px]" />
          )}
        </RailIconButton>
        <div className="mt-0.5 flex h-9 w-9 items-center justify-center">
          <AuthButton />
        </div>
      </div>
    </aside>
  );
}

function RailIconButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className="flex h-9 w-9 items-center justify-center rounded-full text-nav-foreground/80 transition-colors duration-100 hover:bg-nav-hover hover:text-white"
    >
      {children}
    </button>
  );
}
