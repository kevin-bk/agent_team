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
import { ChannelsPage } from "@/features/comm/ChannelsPage";
import { SandboxesPage } from "@/features/sandboxes/SandboxesPage";
import { GuidePage } from "@/features/guide/GuidePage";

const LS_PROFILE = "da.profile";

function viewFromPath(pathname: string): View {
  if (pathname.startsWith("/chat")) return "chat";
  if (pathname.startsWith("/repositories")) return "repos";
  if (pathname.startsWith("/channels")) return "channels";
  if (pathname.startsWith("/sandboxes")) return "sandboxes";
  if (pathname.startsWith("/guide")) return "guide";
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
  const view = viewFromPath(location.pathname);
  const convMatch = location.pathname.match(/^\/chat\/(.+)$/);
  const convId = convMatch ? decodeURIComponent(convMatch[1]) : undefined;

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
    else if (v === "channels") navigate("/channels");
    else if (v === "sandboxes") navigate("/sandboxes");
    else if (v === "guide") navigate("/guide/start");
    else navigate(`/${v}`);
  };

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <NavRail
        view={view}
        onViewChange={goToView}
        dark={dark}
        onToggleTheme={toggle}
      />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <Sidebar
          profile={profile}
          onProfileChange={changeProfile}
          view={view}
          selectedConvId={convId}
          onSelectConv={(id) => navigate(id ? `/chat/${encodeURIComponent(id)}` : "/chat")}
          collapsed={false}
        />
        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
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
          <Route path="channels" element={<ChannelsPage />} />
          <Route path="sandboxes" element={<SandboxesPage />} />
          <Route path="guide" element={<Navigate to="/guide/start" replace />} />
          <Route path="guide/:slug" element={<GuidePage />} />
          <Route path="*" element={<Navigate to="/boards" replace />} />
        </Route>
      </Routes>
    </ConfirmProvider>
  );
}
