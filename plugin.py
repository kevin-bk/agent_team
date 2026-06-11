"""Agent Team plugin registration.

Contributes the platform data tables, the REST API, a sidebar entry, and the
static single-page app that renders the platform UI. The SPA is mounted as a
static ASGI app so it is served on the same port as the rest of the admin app;
when the plugin is disabled, ``PluginDisabledMiddleware`` blocks its routes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from agent_team.spa import SPAStaticFiles
from core.plugin_sdk.base import (
    MenuItem,
    PluginAsgiApp,
    PluginBase,
    PluginMeta,
    ToolFactory,
)

#: Path where the SPA is served. The sidebar entry links here.
SPA_MOUNT_PATH = "/agent-team"

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class AgentTeamPlugin(PluginBase):
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="agent_team",
            version="0.1.0",
            description=(
                "A platform where multiple agents collaborate on work, organised "
                "around a task board with per-task workspaces."
            ),
            author="agent-manager",
        )

    def models(self) -> list:
        from agent_team.features.board.models import (
            AgentTeamActivity,
            AgentTeamBoard,
            AgentTeamBoardMember,
            AgentTeamComment,
            AgentTeamConversation,
            AgentTeamKeySeq,
            AgentTeamRun,
            AgentTeamRunEvent,
            AgentTeamTask,
        )
        from agent_team.features.repos.models import (
            AgentTeamBoardRepo,
            AgentTeamRepo,
        )

        return [
            AgentTeamKeySeq,
            AgentTeamBoard,
            AgentTeamBoardMember,
            AgentTeamTask,
            AgentTeamConversation,
            AgentTeamRun,
            AgentTeamRunEvent,
            AgentTeamComment,
            AgentTeamActivity,
            AgentTeamRepo,
            AgentTeamBoardRepo,
        ]

    def routers(self) -> list[APIRouter]:
        from agent_team.features.board.router import router as board_router
        from agent_team.features.repos.router import router as repos_router
        from agent_team.router import router as platform_router

        return [platform_router, board_router, repos_router]

    def tool_factories(self) -> list[ToolFactory]:
        # Only registered (and therefore offered to agents) while this plugin is
        # enabled — the registry filters factories from disabled plugins.
        return [
            ToolFactory(
                key="enable_agent_team_view_image",
                display_name="View Image",
                description=(
                    "Let the agent see image files (e.g. Jira attachments) in the "
                    "task workspace, since the text file tools cannot show images."
                ),
                category="agent_team",
                default_enabled=True,
                create_tools=_create_view_image_tools,
            ),
            ToolFactory(
                key="enable_agent_team_git_push",
                display_name="Git Push",
                description=(
                    "Let the agent push a board repo's task working copy to its "
                    "remote using the repo's stored credentials. Push is still "
                    "gated per-repo by the admin's allow-push policy."
                ),
                category="agent_team",
                default_enabled=True,
                create_tools=_create_git_tools,
            ),
        ]

    def asgi_apps(self) -> list[PluginAsgiApp]:
        if not _STATIC_DIR.is_dir():
            return []
        return [
            PluginAsgiApp(
                path=SPA_MOUNT_PATH,
                app=SPAStaticFiles(directory=str(_STATIC_DIR), html=True),
                name="agent_team_spa",
            )
        ]

    def menu_items(self) -> list[MenuItem]:
        return [
            MenuItem(
                label="Agent Team",
                url=f"{SPA_MOUNT_PATH}/",
                icon="users",
                order=24,
                key="agent_team",
            )
        ]

    def on_startup(self) -> None:
        # The local backend keeps in-flight runs only in memory, so any run left
        # non-terminal by a previous process is failed with a clear reason.
        # (Schema changes live in ``db_migrations/*.sql`` — the core migration
        # runner applies them automatically before plugins start.)
        import logging

        from agent_team.features.board.runtime.local_backend import (
            reconcile_orphans_sync,
        )

        recovered = reconcile_orphans_sync()
        if recovered:
            logging.getLogger(__name__).info(
                "agent_team: marked %d orphaned run(s) as error on startup", recovered
            )

        # Start the in-process scheduled-pull ticker for board repositories.
        try:
            from agent_team.features.repos.scheduler import start_ticker

            start_ticker()
        except Exception:
            logging.getLogger(__name__).exception(
                "agent_team: failed to start repo pull ticker"
            )

    def on_shutdown(self) -> None:
        try:
            from agent_team.features.repos.scheduler import stop_ticker

            stop_ticker()
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "agent_team: failed to stop repo pull ticker"
            )


def _create_view_image_tools(agent_alias: str, settings: dict[str, str]) -> list:
    from agent_team.features.board.runtime.image_tools import get_image_tools

    return get_image_tools(agent_alias, settings)


def _create_git_tools(agent_alias: str, settings: dict[str, str]) -> list:
    from agent_team.features.board.runtime.git_tools import get_git_tools

    return get_git_tools(agent_alias, settings)
