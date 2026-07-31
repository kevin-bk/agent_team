import {
  Cpu,
  FileText,
  FolderGit2,
  LayoutGrid,
  MessagesSquare,
  Moon,
  Send,
  Sun,
} from "@/components/icons";
import { useMe } from "@/api/hooks";
import { cn } from "@/lib/utils";
import { AuthButton } from "./AuthButton";
import type { View } from "./Sidebar";

interface NavItem {
  id: View;
  label: string;
  icon: typeof LayoutGrid;
}

const ITEMS: NavItem[] = [
  { id: "board", label: "Boards", icon: LayoutGrid },
  { id: "chat", label: "Chats", icon: MessagesSquare },
  { id: "guide", label: "Guide", icon: FileText },
];

const ADMIN_ITEMS: NavItem[] = [
  { id: "repos", label: "Repositories", icon: FolderGit2 },
  { id: "channels", label: "Channels", icon: Send },
  { id: "sandboxes", label: "Sandboxes", icon: Cpu },
];

/**
 * Top navigation bar (Jira's deep-blue product nav, laid out horizontally).
 * Owns the top-level view switch; each entry shows an icon + label with a
 * pill-style active state. Theme toggle and account live on the right.
 */
export function NavRail({
  view,
  onViewChange,
  dark,
  onToggleTheme,
}: {
  view: View;
  onViewChange: (v: View) => void;
  dark: boolean;
  onToggleTheme: () => void;
}) {
  const me = useMe();
  const items = me.data?.is_admin ? [...ITEMS, ...ADMIN_ITEMS] : ITEMS;
  return (
    <header className="flex h-12 w-full shrink-0 items-center gap-1 bg-nav px-3 text-nav-foreground">
      <a
        href="/"
        title="deep-agent"
        className="mr-1 flex items-center gap-2 pr-1"
      >
        <img
          src={`${import.meta.env.BASE_URL}deep-agent-logo.svg`}
          alt="deep-agent"
          className="h-7 w-7 rounded-md"
        />
      </a>

      <nav className="flex min-w-0 items-center gap-0.5">
        {items.map(({ id, label, icon: Icon }) => {
          const active = view === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => onViewChange(id)}
              aria-current={active ? "page" : undefined}
              aria-label={label}
              title={label}
              className={cn(
                "flex h-9 w-9 cursor-pointer items-center justify-center gap-2 rounded-md text-[13.5px] font-medium transition-colors duration-150 xl:w-auto xl:justify-start xl:px-3",
                active
                  ? "bg-white/20 text-white"
                  : "text-nav-foreground/80 hover:bg-nav-hover hover:text-white",
              )}
            >
              <Icon className="h-[18px] w-[18px] shrink-0" />
              <span className="hidden xl:inline">{label}</span>
            </button>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-1.5">
        <button
          type="button"
          onClick={onToggleTheme}
          title={dark ? "Switch to light" : "Switch to dark"}
          aria-label={dark ? "Switch to light" : "Switch to dark"}
          className="flex h-9 w-9 cursor-pointer items-center justify-center rounded-md text-nav-foreground/80 transition-colors duration-150 hover:bg-nav-hover hover:text-white"
        >
          {dark ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
        </button>
        <div className="flex h-9 items-center">
          <AuthButton />
        </div>
      </div>
    </header>
  );
}
