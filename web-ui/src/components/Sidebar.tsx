import { LayoutGrid, Plus } from "@/components/icons";
import { useLocation, useNavigate } from "react-router-dom";
import { useBoards } from "@/api/hooks";
import { JiraAvatar } from "@/components/jira";
import { SessionList } from "@/features/sessions/SessionList";
import { cn } from "@/lib/utils";
import { ProfilePicker } from "./ProfilePicker";

export type View = "board" | "chat" | "repos";

/**
 * The contextual project sidebar that sits between the deep-blue {@link NavRail}
 * and the main content — Jira's two-tier navigation. Its body changes with the
 * active view: a board switcher for Boards, profile + sessions for Chats.
 */
export function Sidebar({
  profile,
  onProfileChange,
  view,
  selectedConvId,
  onSelectConv,
  collapsed,
}: {
  profile: string | undefined;
  onProfileChange: (p: string) => void;
  view: View;
  selectedConvId: string | undefined;
  onSelectConv: (id: string) => void;
  collapsed: boolean;
}) {
  // The repositories page is full-width and needs no contextual sidebar.
  if (collapsed || view === "repos") return null;

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-border bg-surface-1">
      {view === "chat" ? (
        <ChatSidebar
          profile={profile}
          onProfileChange={onProfileChange}
          selectedConvId={selectedConvId}
          onSelectConv={onSelectConv}
        />
      ) : (
        <BoardSidebar />
      )}
    </aside>
  );
}

function ChatSidebar({
  profile,
  onProfileChange,
  selectedConvId,
  onSelectConv,
}: {
  profile: string | undefined;
  onProfileChange: (p: string) => void;
  selectedConvId: string | undefined;
  onSelectConv: (id: string) => void;
}) {
  return (
    <>
      <SidebarHeading title="Chats" subtitle="Agent conversations" />
      <div className="px-3 pb-2 pt-1">
        <ProfilePicker value={profile} onChange={onProfileChange} />
      </div>
      <SessionList
        profile={profile}
        selectedId={selectedConvId}
        onSelect={onSelectConv}
      />
    </>
  );
}

function BoardSidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const boards = useBoards();
  const activeSlug = location.pathname.match(/^\/boards\/([^/]+)/)?.[1];
  const activeBoard = (boards.data ?? []).find((b) => b.slug === activeSlug);

  // Jira-clone project sidebar: a 40px square project avatar + name +
  // category line, then 15px nav rows with a leading icon.
  return (
    <div className="flex h-full flex-col px-4">
      <div className="flex items-center py-6">
        <JiraAvatar
          name={activeBoard?.name ?? "deep-agent"}
          size={40}
          rounded={false}
        />
        <div className="min-w-0 pl-2.5">
          <div className="truncate text-[15px] font-medium text-foreground">
            {activeBoard?.name ?? "deep-agent"}
          </div>
          <div className="truncate text-[13px] text-muted-foreground">
            {activeBoard ? "Kanban project" : "Team & agent boards"}
          </div>
        </div>
      </div>

      <nav className="-mx-1 flex-1 overflow-y-auto pb-4 scrollbar-thin">
        <button
          type="button"
          onClick={() => navigate("/boards")}
          className={cn(
            "flex w-full items-center rounded px-3 py-2 text-left text-[15px] transition-colors duration-100",
            !activeSlug
              ? "bg-surface-3 text-primary"
              : "text-foreground hover:bg-surface-3",
          )}
        >
          <LayoutGrid className="mr-3.5 h-5 w-5" /> All boards
        </button>

        <div className="my-3 border-t-2 border-border-strong/40" />

        {(boards.data ?? []).map((b) => {
          const active = b.slug === activeSlug;
          return (
            <button
              key={b.id}
              type="button"
              onClick={() => navigate(`/boards/${b.slug}`)}
              className={cn(
                "flex w-full items-center rounded px-3 py-2 text-left text-[15px] transition-colors duration-100",
                active
                  ? "bg-surface-3 text-primary"
                  : "text-foreground hover:bg-surface-3",
              )}
              title={b.name}
            >
              <JiraAvatar
                name={b.name}
                size={20}
                rounded={false}
                className="mr-3.5"
              />
              <span className="min-w-0 flex-1 truncate">{b.name}</span>
            </button>
          );
        })}

        {boards.data && boards.data.length === 0 && (
          <button
            type="button"
            onClick={() => navigate("/boards")}
            className="mt-1 flex w-full items-center rounded px-3 py-2 text-[15px] text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground"
          >
            <Plus className="mr-3.5 h-5 w-5" /> Create your first board
          </button>
        )}
      </nav>
    </div>
  );
}

function SidebarHeading({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="px-4 py-4">
      <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
        {title}
      </h2>
      {subtitle && (
        <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
      )}
    </div>
  );
}
