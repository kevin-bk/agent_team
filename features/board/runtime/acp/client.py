"""The single ACP ``Client`` shared by every session on the background loop.

Updates and permission requests carry a ``session_id`` so the manager routes
them to the right handle. File/terminal capabilities are not advertised at
``initialize`` (the engine uses its own on-disk tools), so those callbacks fail
clearly if ever invoked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_team.features.board.runtime.acp.content import pick_permission_option_id

if TYPE_CHECKING:
    from agent_team.features.board.runtime.acp.manager import AcpSessionManager


def build_manager_client(manager: AcpSessionManager) -> Any:
    """Create the ACP ``Client`` bound to ``manager``."""
    from acp import Client, RequestError, RequestPermissionResponse

    class _ManagerClient(Client):
        async def session_update(self, session_id: str, update: Any, **_: Any) -> None:
            manager._route_update(session_id, update)

        async def request_permission(
            self, options: Any, session_id: str, tool_call: Any, **_: Any
        ) -> Any:
            # Honour the run's permission mode: auto-approve picks an allow
            # option; read-only answers ``cancelled`` so the agent cannot act.
            if manager._should_approve(session_id):
                option_id = pick_permission_option_id(options)
                if option_id is not None:
                    return RequestPermissionResponse.model_validate(
                        {"outcome": {"outcome": "selected", "optionId": option_id}}
                    )
            return RequestPermissionResponse.model_validate(
                {"outcome": {"outcome": "cancelled"}}
            )

        async def read_text_file(self, *_: Any, **__: Any) -> Any:
            raise RequestError.method_not_found("fs/read_text_file")

        async def write_text_file(self, *_: Any, **__: Any) -> Any:
            raise RequestError.method_not_found("fs/write_text_file")

        async def create_terminal(self, *_: Any, **__: Any) -> Any:
            raise RequestError.method_not_found("terminal/create")

        async def terminal_output(self, *_: Any, **__: Any) -> Any:
            raise RequestError.method_not_found("terminal/output")

        async def wait_for_terminal_exit(self, *_: Any, **__: Any) -> Any:
            raise RequestError.method_not_found("terminal/wait_for_exit")

        async def kill_terminal(self, *_: Any, **__: Any) -> Any:
            raise RequestError.method_not_found("terminal/kill")

        async def release_terminal(self, *_: Any, **__: Any) -> Any:
            raise RequestError.method_not_found("terminal/release")

    return _ManagerClient()
