"""agent-team-runtime-server — the in-sandbox ACP bridge (Phase 2, Strategy B).

Runs *inside* an OpenSandbox sandbox, next to the workspace and the coding CLI.
For each WebSocket turn it drives the SAME :class:`DirectCliRun` the host uses,
so every ACP frame (text, thinking, tool cards, live plan checklist, usage) is
produced identically — then streams those ``(event_type, data)`` frames back to
the host worker (:class:`SidecarAcpWorker`) over the socket.

Wire protocol: :mod:`agent_team.features.board.runtime.sandbox.sidecar_protocol`.

Run (baked into the image entrypoint / started on demand by the host):

    agent-team-runtime-server --host 0.0.0.0 --port 8871

Only the ``agent_team`` runtime subtree (ACP stack + sidecar protocol) needs to be
importable in the image — **not** the wider app. ACP session state persists via
:mod:`~agent_team.features.board.runtime.acp.store`, which uses a standalone
sandbox-local SQLite when ``AGENT_TEAM_ACP_STORE_DB`` is set (baked into the
image), so nothing here touches ``core`` / ``plugins`` / ``src``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from agent_team.features.board.runtime.acp import DirectCliRun
from agent_team.features.board.runtime.acp.engines import ENGINES
from agent_team.features.board.runtime.sandbox import sidecar_protocol as proto

logger = logging.getLogger("agent_team_runtime_server")


def build_app():
    """Build the FastAPI app (imported lazily so ``--help`` works without deps)."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect

    app = FastAPI(title="agent-team-runtime-server")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "engines": sorted(ENGINES)}

    @app.websocket("/acp")
    async def acp(ws: WebSocket) -> None:
        await ws.accept()
        await ws.send_text(proto.encode(proto.hello(engines=sorted(ENGINES))))
        try:
            first = await ws.receive_text()
        except WebSocketDisconnect:
            return
        try:
            req = proto.decode(first)
        except ValueError:
            await ws.send_text(proto.encode(proto.error("malformed turn request")))
            return
        if req.get("type") != proto.MSG_TURN:
            await ws.send_text(proto.encode(proto.error("expected a turn request")))
            return

        await _run_turn(ws, req)

    return app


async def _run_turn(ws, req: dict) -> None:
    """Drive one prompt turn and stream its frames; honour an async cancel msg."""
    from fastapi import WebSocketDisconnect

    cancel_event = asyncio.Event()

    async def _watch_cancel() -> None:
        try:
            while True:
                raw = await ws.receive_text()
                msg = proto.decode(raw)
                if msg.get("type") == proto.MSG_CANCEL:
                    cancel_event.set()
                    return
        except (WebSocketDisconnect, ValueError, RuntimeError):
            cancel_event.set()

    watcher = asyncio.create_task(_watch_cancel())
    run = DirectCliRun(
        engine=str(req.get("engine") or ""),
        prompt=str(req.get("prompt") or ""),
        cwd=str(req.get("cwd") or "/workspace"),
        thread_id=str(req.get("thread_id") or ""),
        auto_approve=bool(req.get("auto_approve", True)),
        idle_timeout_seconds=int(req.get("idle_timeout_seconds") or 0),
        mcp_config=req.get("mcp_config"),
        secrets=list(req.get("secrets") or []),
    )
    try:
        async for event_type, data in run.stream_frames(cancel_event):
            await ws.send_text(proto.encode(proto.frame(event_type, data)))
        await ws.send_text(proto.encode(proto.result(
            final_text=run.final_text,
            cancelled=run.cancelled or cancel_event.is_set(),
            ok=run.ok,
            usage=run.usage,
            cli_usage_text=run.cli_usage_text,
        )))
    except WebSocketDisconnect:
        cancel_event.set()
    except Exception as exc:  # noqa: BLE001 — surface, never crash the socket loop
        logger.exception("turn failed")
        with _suppress():
            await ws.send_text(proto.encode(proto.error(f"turn failed: {exc}")))
    finally:
        watcher.cancel()
        with _suppress():
            await watcher


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):  # noqa: ANN002
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="agent-team ACP sidecar bridge server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8871)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    import uvicorn

    logging.basicConfig(level=args.log_level.upper())
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
