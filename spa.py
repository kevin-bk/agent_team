"""Static file app for the SPA with client-side routing fallback.

A plain ``StaticFiles`` returns 404 for deep links like ``/agent-team/boards/x``
because no such file exists on disk. The SPA owns those routes, so we fall back
to ``index.html`` on any miss and let the in-browser router resolve the path.
"""

from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise
