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


def _build_loop_capture_app():
    """A tiny mounted app whose lifespan captures the loop and starts autopilot.

    ``plugin.on_startup`` runs in ``create_app`` *before* the event loop exists,
    and — critically — under ``uvicorn --workers`` / PM2 multi-instance it can
    run in a process that never serves the ASGI app (so its loop is never
    captured). The core lifespan, by contrast, is entered *inside* the running
    loop of an actual serving worker (see ``startup_mounted_apps``).

    By both capturing the loop *and* starting the autopilot ticker here, the
    ticker is guaranteed to live in the same process whose loop we captured, so
    ``dispatch_start`` can always hand runs to a live loop. The ticker's own
    ``fcntl`` lock still ensures only one worker actually runs it. The repo-pull
    ticker stays in ``on_startup`` because it does pure-sync work and never
    touches the loop.
    """
    import logging as _logging
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    log = _logging.getLogger(__name__)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        try:
            from agent_team.features.board.runtime.dispatch import capture_main_loop

            capture_main_loop()
        except Exception:  # noqa: BLE001 — never block app startup on this
            log.exception("agent_team: failed to capture main loop")
        try:
            from agent_team.features.board.autopilot_scheduler import (
                start_ticker as start_autopilot_ticker,
            )

            start_autopilot_ticker()
        except Exception:  # noqa: BLE001
            log.exception("agent_team: failed to start autopilot ticker")
        try:
            yield
        finally:
            try:
                from agent_team.features.board.autopilot_scheduler import (
                    stop_ticker as stop_autopilot_ticker,
                )

                stop_autopilot_ticker()
            except Exception:  # noqa: BLE001
                pass

    return FastAPI(lifespan=_lifespan)


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
            AgentTeamAutopilot,
            AgentTeamBoard,
            AgentTeamBoardMember,
            AgentTeamComment,
            AgentTeamConversation,
            AgentTeamKeySeq,
            AgentTeamRun,
            AgentTeamRunEvent,
            AgentTeamTask,
            AgentTeamTaskSchedule,
            AgentTeamToolOutput,
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
            AgentTeamToolOutput,
            AgentTeamAutopilot,
            AgentTeamTaskSchedule,
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
            ToolFactory(
                key="enable_agent_team_set_task_status",
                display_name="Set Task Status",
                description=(
                    "Let the agent move its own task between the board's columns "
                    "(e.g. mark it review/done/blocked). Useful with autopilot so "
                    "the agent can advance its task itself."
                ),
                category="agent_team",
                default_enabled=True,
                create_tools=_create_status_tools,
            ),
        ]

    def asgi_apps(self) -> list[PluginAsgiApp]:
        # Always mount the loop-capture app (even without the SPA build) so the
        # autopilot ticker can dispatch runs without depending on a board
        # endpoint being hit first.
        apps = [
            PluginAsgiApp(
                path="/_agent_team_internal",
                app=_build_loop_capture_app(),
                name="agent_team_internal",
            )
        ]
        if not _STATIC_DIR.is_dir():
            return apps
        return apps + [
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

        # NOTE: the board autopilot ticker is intentionally NOT started here.
        # ``on_startup`` runs at ``create_app`` time (before the event loop) and,
        # under ``uvicorn --workers``/PM2, possibly in a process that never serves
        # the app — so its captured loop would be missing and runs could never be
        # dispatched. It is started from the loop-capture app's lifespan instead
        # (see ``_build_loop_capture_app``), guaranteeing the ticker and the
        # captured loop share a process.

    def on_shutdown(self) -> None:
        import logging

        try:
            from agent_team.features.repos.scheduler import stop_ticker

            stop_ticker()
        except Exception:
            logging.getLogger(__name__).exception(
                "agent_team: failed to stop repo pull ticker"
            )

        try:
            from agent_team.features.board.autopilot_scheduler import (
                stop_ticker as stop_autopilot_ticker,
            )

            stop_autopilot_ticker()
        except Exception:
            logging.getLogger(__name__).exception(
                "agent_team: failed to stop autopilot ticker"
            )


def _create_view_image_tools(agent_alias: str, settings: dict[str, str]) -> list:
    from agent_team.features.board.runtime.image_tools import get_image_tools

    return get_image_tools(agent_alias, settings)


def _create_git_tools(agent_alias: str, settings: dict[str, str]) -> list:
    from agent_team.features.board.runtime.git_tools import get_git_tools

    return get_git_tools(agent_alias, settings)


def _create_status_tools(agent_alias: str, settings: dict[str, str]) -> list:
    from agent_team.features.board.runtime.status_tools import get_status_tools

    return get_status_tools(agent_alias, settings)
