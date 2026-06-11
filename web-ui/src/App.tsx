import { useEffect, useState } from "react";
import {
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { useBoards, useProfiles } from "@/api/hooks";
import { CommandPalette } from "@/components/CommandPalette";
import { ConfirmProvider } from "@/components/ConfirmDialog";
import { NavRail } from "@/components/NavRail";
import { Sidebar, type View } from "@/components/Sidebar";
import { Spinner } from "@/components/ui/spinner";
import { useTheme } from "@/lib/useTheme";
import { BoardView } from "@/features/board/BoardView";
import { BoardsView } from "@/features/board/BoardsView";
import { ChatView } from "@/features/chat/ChatView";
import { ReposPage } from "@/features/repos/ReposPage";

const LS_PROFILE = "da.profile";
const LS_COLLAPSED = "da.sidebar.collapsed";

function viewFromPath(pathname: string): View {
  if (pathname.startsWith("/chat")) return "chat";
  if (pathname.startsWith("/repositories")) return "repos";
  return "board";
}

/** App shell: persistent sidebar + the active route in <main>. */
function Shell() {
  const { data: profiles } = useProfiles();
  const location = useLocation();
  const navigate = useNavigate();
  const { dark, toggle } = useTheme();

  const [profile, setProfile] = useState<string | undefined>(
    () => localStorage.getItem(LS_PROFILE) ?? undefined,
  );
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem(LS_COLLAPSED) === "1",
  );

  const view = viewFromPath(location.pathname);
  const convMatch = location.pathname.match(/^\/chat\/(.+)$/);
  const convId = convMatch ? decodeURIComponent(convMatch[1]) : undefined;

  const toggleCollapsed = () =>
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem(LS_COLLAPSED, next ? "1" : "0");
      return next;
    });

  // Default to the first available profile once loaded.
  useEffect(() => {
    if (!profiles || profiles.length === 0) return;
    if (!profile || !profiles.some((p) => p.name === profile)) {
      setProfile(profiles[0].name);
    }
  }, [profiles, profile]);

  const changeProfile = (p: string) => {
    setProfile(p);
    localStorage.setItem(LS_PROFILE, p);
    if (view === "chat") navigate("/chat");
  };

  const goToView = (v: View) => {
    if (v === "board") navigate("/boards");
    else if (v === "repos") navigate("/repositories");
    else navigate(`/${v}`);
  };

  return (
    <div className="flex h-full w-full overflow-hidden">
      <NavRail
        view={view}
        onViewChange={goToView}
        dark={dark}
        onToggleTheme={toggle}
        collapsed={collapsed}
        onToggleCollapse={toggleCollapsed}
      />
      <Sidebar
        profile={profile}
        onProfileChange={changeProfile}
        view={view}
        selectedConvId={convId}
        onSelectConv={(id) => navigate(id ? `/chat/${encodeURIComponent(id)}` : "/chat")}
        collapsed={collapsed}
      />
      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
      <CommandPalette />
    </div>
  );
}

function ChatRoute() {
  const { convId } = useParams<{ convId?: string }>();
  return <ChatView convId={convId} />;
}

/**
 * Pretty URLs: the path carries the board *slug* and the task *human key*
 * (e.g. /boards/sprint-board/tasks/T-1) instead of opaque UUIDs. We resolve
 * the slug to the board id on the client (no extra API surface).
 */
function BoardRoute() {
  const { boardSlug, taskKey } = useParams<{ boardSlug: string; taskKey?: string }>();
  const navigate = useNavigate();
  const boards = useBoards();

  if (!boardSlug) return <Navigate to="/boards" replace />;

  const board = boards.data?.find((b) => b.slug === boardSlug);
  if (boards.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (!board) return <Navigate to="/boards" replace />;

  return (
    <BoardView
      boardId={board.id}
      cockpitTaskKey={taskKey ?? null}
      onBack={() => navigate("/boards")}
      onOpenTask={(key) => navigate(`/boards/${boardSlug}/tasks/${key}`)}
      onCloseTask={() => navigate(`/boards/${boardSlug}`)}
    />
  );
}

export function App() {
  return (
    <ConfirmProvider>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Navigate to="/boards" replace />} />
          <Route path="boards" element={<BoardsView />} />
          <Route path="boards/:boardSlug" element={<BoardRoute />} />
          <Route path="boards/:boardSlug/tasks/:taskKey" element={<BoardRoute />} />
          <Route path="chat" element={<ChatRoute />} />
          <Route path="chat/:convId" element={<ChatRoute />} />
          <Route path="repositories" element={<ReposPage />} />
          <Route path="*" element={<Navigate to="/boards" replace />} />
        </Route>
      </Routes>
    </ConfirmProvider>
  );
}
