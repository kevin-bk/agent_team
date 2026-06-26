"""The agent-team-owned ACP session manager.

Owns a single background asyncio loop on which every ACP connection lives, caches
live sessions keyed by a conversation key, resumes them across restarts via the
durable store, and drives one prompt turn at a time per key. Beyond the baseline
turn it adds:

* **secret masking** of streamed text / tool params / output at ingestion,
* **MCP pass-through** — configured MCP servers are attached to the session so
  the engine connects to them itself,
* **fork sub-queries** (:meth:`AcpSessionManager.ask`) for an out-of-band probe
  on the same conversation without disturbing the main turn.

The progress of a turn is shuttled to the caller through a thread-safe queue of
``claude_acp_*`` event dicts; the translator in :mod:`...acp.run` turns those
into ``AgentEvent`` frames.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from agent_team.features.board.runtime.acp import store as _store
from agent_team.features.board.runtime.acp import usage as _usage
from agent_team.features.board.runtime.acp.client import build_manager_client
from agent_team.features.board.runtime.acp.content import (
    TOOL_PARAM_LIMIT,
    format_plan_entries,
    strip_nul,
    text_from_content_block,
    tool_input_summary,
    tool_output_text,
)
from agent_team.features.board.runtime.acp.errors import (
    classify_turn_error,
    install_hint,
)
from agent_team.features.board.runtime.acp.mcp import mcp_config_to_acp_servers

logger = logging.getLogger(__name__)

_DEFAULT_IDLE_TTL_SECONDS = 600
_MAX_LIVE_SESSIONS = 16
#: asyncio StreamReader default limit is 64 KB; ACP sends each JSON-RPC message
#: as one newline-terminated line, so large responses overflow it.
_ACP_STDIO_LIMIT = 100 * 1024 * 1024
#: Chars of a tool's output forwarded inline on a progress event.
_OUTPUT_CHAR_LIMIT = 16_000

_ALL_STREAM_EVENTS: frozenset[str] = frozenset(
    {"progress", "thought", "tool_start", "tool_progress", "plan", "usage"}
)

_EVENT_TYPE_TO_STREAM_KEY: dict[str, str] = {
    "AgentMessageChunk": "progress",
    "AgentThoughtChunk": "thought",
    "ToolCallStart": "tool_start",
    "ToolCallProgress": "tool_progress",
    "AgentPlanUpdate": "plan",
    "UsageUpdate": "usage",
}


# ---------------------------------------------------------------------------
# Background event loop owning all ACP connections
# ---------------------------------------------------------------------------


class BackgroundLoop:
    """A singleton daemon thread running one asyncio loop for all ACP work."""

    _instance: BackgroundLoop | None = None
    _guard = threading.Lock()

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="agent-team-acp-loop", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    @classmethod
    def instance(cls) -> BackgroundLoop:
        with cls._guard:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def submit(self, coro: Any) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


# ---------------------------------------------------------------------------
# Session handle + small duck-typed helpers
# ---------------------------------------------------------------------------


def _extract_session_id(session: Any) -> Any:
    return getattr(session, "session_id", None) or getattr(session, "sessionId", None)


def _agent_supports_load(init_response: Any) -> bool:
    caps = getattr(init_response, "agent_capabilities", None) or getattr(
        init_response, "agentCapabilities", None
    )
    if caps is None:
        return False
    return bool(
        getattr(caps, "load_session", None) or getattr(caps, "loadSession", None)
    )


def _mcp_capabilities(init_response: Any) -> Any:
    caps = getattr(init_response, "agent_capabilities", None) or getattr(
        init_response, "agentCapabilities", None
    )
    if caps is None:
        return None
    return getattr(caps, "mcp_capabilities", None) or getattr(
        caps, "mcpCapabilities", None
    )


def _agent_supports_fork(init_response: Any) -> bool:
    """Whether the agent advertised the ``session.fork`` capability at init.

    Fork branches a *new* session from the live one's current head (inheriting
    the conversation so far) without touching the parent turn. It is optional in
    ACP, so probe before relying on it and degrade gracefully when absent.
    """
    caps = getattr(init_response, "agent_capabilities", None) or getattr(
        init_response, "agentCapabilities", None
    )
    if caps is None:
        return False
    session_caps = getattr(caps, "session_capabilities", None) or getattr(
        caps, "sessionCapabilities", None
    )
    if session_caps is None:
        return False
    return getattr(session_caps, "fork", None) is not None


class _SessionHandle:
    """One live ACP subprocess + session, owned by the background loop."""

    __slots__ = (
        "cm",
        "conn",
        "proc",
        "session_id",
        "cwd",
        "auto_approve",
        "supports_fork",
        "mask",
        "collector",
        "progress_q",
        "prompting",
        "busy",
        "cancelled",
        "last_used",
    )

    def __init__(
        self,
        *,
        cm: Any,
        conn: Any,
        proc: Any,
        session_id: Any,
        cwd: str | None,
        auto_approve: bool,
        supports_fork: bool = False,
        mask: Callable[[str], str] | None = None,
    ) -> None:
        self.cm = cm
        self.conn = conn
        self.proc = proc
        self.session_id = session_id
        self.cwd = cwd
        self.auto_approve = auto_approve
        self.supports_fork = supports_fork
        self.mask = mask
        self.collector: list[str] = []
        self.progress_q: queue.Queue | None = None
        self.prompting = False
        self.busy = False
        self.cancelled = False
        self.last_used = time.monotonic()

    def is_alive(self) -> bool:
        proc = self.proc
        if proc is None:
            return True
        return getattr(proc, "returncode", None) is None


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------


class AcpSessionManager:
    """Caches and reaps live ACP sessions. All methods run on the background loop."""

    def __init__(self, *, idle_ttl: int, max_sessions: int) -> None:
        self._idle_ttl = max(30, idle_ttl)
        self._max_sessions = max(1, max_sessions)
        self._client: Any = None
        self._sessions: dict[str, _SessionHandle] = {}
        self._by_session_id: dict[Any, _SessionHandle] = {}
        self._active: dict[str, dict[int, _SessionHandle]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._reaper_started = False
        # session_id -> list collecting a fork's text (for ask()).
        self._fork_collectors: dict[Any, list[str]] = {}

    # --- update routing (called from client coroutines on the loop) ---

    def _route_update(self, session_id: Any, update: Any) -> None:
        # Fork sub-query updates go to their own collector (see ask()).
        collector = self._fork_collectors.get(session_id)
        if collector is not None:
            if type(update).__name__ == "AgentMessageChunk":
                text = text_from_content_block(getattr(update, "content", None))
                if text:
                    collector.append(text)
            return

        handle = self._by_session_id.get(session_id)
        if handle is None or not handle.prompting:
            return
        stream_key = _EVENT_TYPE_TO_STREAM_KEY.get(type(update).__name__)
        if stream_key is None:
            return
        mask = handle.mask
        event = self._build_event(type(update).__name__, update, handle, mask)
        if event is not None and handle.progress_q is not None:
            try:
                handle.progress_q.put_nowait(event)
            except Exception:
                logger.debug("acp progress queue put failed", exc_info=True)

    def _build_event(
        self,
        update_type: str,
        update: Any,
        handle: _SessionHandle,
        mask: Callable[[str], str] | None,
    ) -> dict[str, str] | None:
        def _mask(text: str) -> str:
            return mask(text) if mask is not None else text

        if update_type == "AgentMessageChunk":
            text = _mask(text_from_content_block(getattr(update, "content", None)))
            if text:
                handle.collector.append(text)
                return {"claude_acp_progress": text}
        elif update_type == "AgentThoughtChunk":
            text = _mask(text_from_content_block(getattr(update, "content", None)))
            if text:
                return {"claude_acp_thought": text}
        elif update_type == "ToolCallStart":
            title = _mask(str(getattr(update, "title", "") or ""))
            kind = str(getattr(update, "kind", "") or "")
            tool_id = str(getattr(update, "tool_call_id", "") or "")
            if title:
                cmd = strip_nul(
                    _mask(tool_input_summary(getattr(update, "raw_input", None)))
                )[:TOOL_PARAM_LIMIT]
                return {
                    "claude_acp_tool_start": f"{kind}\x00{title}\x00{tool_id}\x00{cmd}"
                }
        elif update_type == "ToolCallProgress":
            title = _mask(str(getattr(update, "title", "") or ""))
            status = str(getattr(update, "status", "") or "")
            tool_id = str(getattr(update, "tool_call_id", "") or "")
            if tool_id or title:
                output = strip_nul(_mask(tool_output_text(update)))[:_OUTPUT_CHAR_LIMIT]
                cmd = strip_nul(
                    _mask(tool_input_summary(getattr(update, "raw_input", None)))
                )[:TOOL_PARAM_LIMIT]
                return {
                    "claude_acp_tool_progress": (
                        f"{tool_id}\x00{status}\x00{title}\x00{output}\x00{cmd}"
                    )
                }
        elif update_type == "AgentPlanUpdate":
            text = _mask(format_plan_entries(getattr(update, "entries", None)))
            if text:
                return {"claude_acp_plan": text}
        elif update_type == "UsageUpdate":
            used = int(getattr(update, "used", 0) or 0)
            size = int(getattr(update, "size", 0) or 0)
            return {
                "claude_acp_usage": _usage.format_gauge(
                    used, size, getattr(update, "cost", None)
                )
            }
        return None

    def _should_approve(self, session_id: Any) -> bool:
        handle = self._by_session_id.get(session_id)
        return bool(handle and handle.auto_approve)

    # --- cancellation ---

    def _register_active(self, cancel_id: str, handle: _SessionHandle) -> None:
        if cancel_id:
            self._active.setdefault(cancel_id, {})[id(handle)] = handle

    def _deregister_active(self, cancel_id: str, handle: _SessionHandle) -> None:
        if not cancel_id:
            return
        bucket = self._active.get(cancel_id)
        if bucket is not None:
            bucket.pop(id(handle), None)
            if not bucket:
                self._active.pop(cancel_id, None)

    async def cancel(self, cancel_id: str) -> int:
        """Send ``session/cancel`` to every live run bound to ``cancel_id``.

        Graceful: the engine halts and its in-flight ``prompt`` returns with
        ``stop_reason="cancelled"`` (the caller then drains it). Returns the
        number of sessions signalled.
        """
        handles = list((self._active.get(cancel_id) or {}).values())
        signalled = 0
        for handle in handles:
            handle.cancelled = True
            session_id = handle.session_id
            conn = handle.conn
            if session_id is None or conn is None:
                continue
            try:
                result = conn.cancel(session_id)
                if asyncio.iscoroutine(result):
                    await result
                signalled += 1
            except Exception:
                logger.debug("acp session cancel failed", exc_info=True)
        return signalled

    # --- internals ---

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _ensure_reaper(self) -> None:
        if not self._reaper_started:
            self._reaper_started = True
            asyncio.create_task(self._reaper())

    async def _client_instance(self) -> Any:
        if self._client is None:
            self._client = build_manager_client(self)
        return self._client

    async def _create_session(
        self,
        *,
        cwd: str | None,
        command: str,
        args: list[str],
        env_overrides: dict[str, str],
        auto_approve: bool,
        mask: Callable[[str], str] | None = None,
        mcp_config: dict[str, Any] | None = None,
        resume_session_id: str | None = None,
    ) -> tuple[_SessionHandle, bool]:
        """Spawn a subprocess and open (or resume) an ACP session.

        When ``resume_session_id`` is given and the engine advertises
        ``loadSession``, the existing session is resumed so the conversation
        continues with the same id; any failure falls back to a fresh session.
        Configured MCP servers are attached, gated on the session's advertised
        transport capabilities.         Returns ``(handle, resumed)``.
        """
        from acp import PROTOCOL_VERSION, spawn_agent_process

        client = await self._client_instance()
        child_env = dict(os.environ)
        child_env.update({k: v for k, v in (env_overrides or {}).items() if v})
        cm = spawn_agent_process(
            client,
            command,
            *args,
            transport_kwargs={"limit": _ACP_STDIO_LIMIT},
            env=child_env,
        )
        conn, proc = await cm.__aenter__()
        resumed = False
        try:
            init = await conn.initialize(protocol_version=PROTOCOL_VERSION)
            mcp_servers = mcp_config_to_acp_servers(mcp_config, _mcp_capabilities(init))
            can_load = _agent_supports_load(init)
            supports_fork = _agent_supports_fork(init)
            session_id: Any = None
            if resume_session_id and can_load:
                try:
                    await conn.load_session(
                        cwd=cwd,
                        session_id=resume_session_id,
                        mcp_servers=mcp_servers,
                    )
                    session_id = resume_session_id
                    resumed = True
                except Exception:
                    logger.warning(
                        "acp load_session(%s) failed; starting a new session",
                        resume_session_id,
                        exc_info=True,
                    )
            if session_id is None:
                session = await conn.new_session(cwd=cwd, mcp_servers=mcp_servers)
                session_id = _extract_session_id(session)
        except BaseException:
            await self._safe_aexit(cm, proc)
            raise
        handle = _SessionHandle(
            cm=cm,
            conn=conn,
            proc=proc,
            session_id=session_id,
            cwd=cwd,
            auto_approve=auto_approve,
            supports_fork=supports_fork,
            mask=mask,
        )
        if session_id is not None:
            self._by_session_id[session_id] = handle
        return handle, resumed

    async def _do_prompt(
        self, handle: _SessionHandle, prompt: str, timeout_seconds: int
    ) -> str:
        from acp import text_block

        # Let a resumed session's session/load history replay flush (and be
        # dropped) before capture begins for this turn.
        handle.prompting = False
        handle.collector = []
        for _ in range(3):
            await asyncio.sleep(0)
        handle.collector = []

        session_id = handle.session_id
        logger.info(
            "ACP prompt (session_id=%s, timeout=%ss)", session_id, timeout_seconds
        )
        handle.prompting = True
        try:
            response = await asyncio.wait_for(
                handle.conn.prompt(
                    session_id=session_id,
                    prompt=[text_block(prompt)],
                    message_id=str(uuid.uuid4()),
                ),
                timeout=timeout_seconds,
            )
        finally:
            handle.prompting = False
        stop_reason = str(
            getattr(response, "stop_reason", "")
            or getattr(response, "stopReason", "")
            or ""
        )
        usage_obj = getattr(response, "usage", None)
        if usage_obj is not None and handle.progress_q is not None:
            try:
                handle.progress_q.put_nowait(
                    {"claude_acp_usage_final": _usage.format_totals(usage_obj)}
                )
            except Exception:
                logger.debug("acp usage-final queue put failed", exc_info=True)
        text = "".join(handle.collector).strip()
        if not text:
            return f"(no text output; stop_reason={stop_reason or 'unknown'})"
        # Re-mask the joined reply: per-chunk masking misses a secret split across
        # two streamed chunks (neither half matches alone), but the joined text
        # does — this is the last boundary before the text is returned/persisted.
        if handle.mask is not None:
            text = handle.mask(text)
        return text

    async def _safe_aexit(self, cm: Any, proc: Any) -> None:
        try:
            if cm is not None:
                await cm.__aexit__(None, None, None)
        except Exception:
            logger.warning("acp session teardown error", exc_info=True)
        try:
            if proc is not None and getattr(proc, "returncode", None) is None:
                proc.kill()
        except Exception:
            logger.debug("acp subprocess kill failed", exc_info=True)

    async def _teardown(self, handle: _SessionHandle) -> None:
        if handle.session_id is not None:
            self._by_session_id.pop(handle.session_id, None)
        await self._safe_aexit(handle.cm, handle.proc)

    def _enforce_capacity(self, *, exclude: str) -> None:
        while len(self._sessions) > self._max_sessions:
            candidates = [
                (handle.last_used, key)
                for key, handle in self._sessions.items()
                if key != exclude and not handle.busy
            ]
            if not candidates:
                break
            _, victim = min(candidates)
            handle = self._sessions.pop(victim, None)
            self._locks.pop(victim, None)
            if handle is not None:
                asyncio.create_task(self._teardown(handle))

    async def _reaper(self) -> None:
        interval = min(60, max(15, self._idle_ttl // 4))
        while True:
            try:
                await asyncio.sleep(interval)
                now = time.monotonic()
                for key in list(self._sessions.keys()):
                    handle = self._sessions.get(key)
                    if handle is None or handle.busy:
                        continue
                    if now - handle.last_used <= self._idle_ttl:
                        continue
                    lock = self._locks.get(key)
                    if lock is not None and lock.locked():
                        continue
                    self._sessions.pop(key, None)
                    self._locks.pop(key, None)
                    await self._teardown(handle)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("acp reaper iteration failed", exc_info=True)

    # --- public coroutine API ---

    async def run_prompt(
        self,
        *,
        key: str,
        prompt: str,
        cwd: str | None,
        command: str,
        args: list[str],
        env_overrides: dict[str, str],
        auto_approve: bool,
        timeout_seconds: int,
        create_timeout: int,
        progress_q: queue.Queue | None = None,
        cancel_id: str = "",
        mask: Callable[[str], str] | None = None,
        mcp_config: dict[str, Any] | None = None,
    ) -> str:
        self._ensure_reaper()
        params = {
            "cwd": cwd,
            "command": command,
            "args": args,
            "env_overrides": env_overrides,
            "auto_approve": auto_approve,
            "mask": mask,
            "mcp_config": mcp_config,
        }
        async with self._lock_for(key):
            handle = self._sessions.get(key)
            if handle is not None and not handle.is_alive():
                await self._teardown(handle)
                self._sessions.pop(key, None)
                handle = None
            if handle is None:
                stored = await asyncio.to_thread(_store.load_session_id, key)
                resume_id = stored[0] if stored else None
                handle, resumed = await asyncio.wait_for(
                    self._create_session(**params, resume_session_id=resume_id),
                    timeout=create_timeout,
                )
                self._sessions[key] = handle
                if not resumed and handle.session_id is not None:
                    await asyncio.to_thread(
                        _store.save_session_id,
                        key,
                        str(handle.session_id),
                        cwd,
                    )
                self._enforce_capacity(exclude=key)
            handle.busy = True
            handle.cancelled = False
            handle.last_used = time.monotonic()
            handle.progress_q = progress_q
            handle.mask = mask
            self._register_active(cancel_id, handle)
            try:
                return await self._do_prompt(handle, prompt, timeout_seconds)
            except BaseException:
                # Any mid-run failure (incl. cancel/timeout) evicts the session so
                # a half-finished subprocess is never reused; the next turn rebuilds
                # fresh from the stored session id (the workspace is the carried
                # context). This is the cancel → restart recovery.
                await self._teardown(handle)
                self._sessions.pop(key, None)
                raise
            finally:
                self._deregister_active(cancel_id, handle)
                handle.progress_q = None
                handle.busy = False
                handle.last_used = time.monotonic()

    async def ask(
        self,
        *,
        key: str,
        question: str,
        timeout_seconds: int,
    ) -> str | None:
        """Fork the live session for ``key`` and run a one-off sub-query.

        Returns the fork's answer text (masked), or ``None`` when there is no live
        session to fork or the fork is unsupported. The main turn is untouched.
        """
        handle = self._sessions.get(key)
        if handle is None or handle.conn is None or handle.session_id is None:
            return None
        # Fork is optional in ACP. If the engine never advertised it at init,
        # skip the round-trip and let the caller fall back instead of eating a
        # RequestError. (A fork inherits the parent's context up to its current
        # head and shares the same cwd — it is not an isolated workspace copy.)
        if not handle.supports_fork:
            logger.debug("acp engine does not support session fork; skipping ask()")
            return None
        from acp import text_block

        conn = handle.conn
        try:
            fork = await conn.fork_session(
                cwd=handle.cwd, session_id=handle.session_id
            )
        except Exception:
            logger.warning("acp fork_session failed", exc_info=True)
            return None
        fork_id = _extract_session_id(fork)
        if fork_id is None:
            return None
        collector: list[str] = []
        self._fork_collectors[fork_id] = collector
        try:
            await asyncio.wait_for(
                conn.prompt(prompt=[text_block(question)], session_id=fork_id),
                timeout=timeout_seconds,
            )
        except Exception:
            logger.warning("acp fork prompt failed", exc_info=True)
            return None
        finally:
            self._fork_collectors.pop(fork_id, None)
        text = "".join(collector).strip()
        if handle.mask is not None:
            text = handle.mask(text)
        return text or None

    async def aclose_all(self) -> None:
        for key in list(self._sessions.keys()):
            handle = self._sessions.pop(key, None)
            if handle is not None:
                await self._teardown(handle)
        self._locks.clear()


# ---------------------------------------------------------------------------
# Module singletons + sync entry points
# ---------------------------------------------------------------------------

_manager: AcpSessionManager | None = None
_manager_guard = threading.Lock()


def get_manager() -> AcpSessionManager:
    global _manager
    with _manager_guard:
        if _manager is None:
            _manager = AcpSessionManager(
                idle_ttl=_DEFAULT_IDLE_TTL_SECONDS, max_sessions=_MAX_LIVE_SESSIONS
            )
            atexit.register(_shutdown_manager)
        return _manager


def _shutdown_manager() -> None:
    manager = _manager
    if manager is None:
        return
    try:
        future = BackgroundLoop.instance().submit(manager.aclose_all())
        future.result(timeout=10)
    except Exception:
        logger.debug("acp manager shutdown best-effort failed", exc_info=True)


async def manager_run_ok(
    *,
    key: str,
    prompt: str,
    cwd: str | None,
    command: str,
    args: list[str],
    env_overrides: dict[str, str],
    auto_approve: bool,
    timeout_seconds: int,
    create_timeout: int,
    progress_q: queue.Queue | None = None,
    cancel_id: str = "",
    mask: Callable[[str], str] | None = None,
    mcp_config: dict[str, Any] | None = None,
    agent_label: str = "ACP",
) -> tuple[str, bool]:
    """Run a prompt and return ``(text, ok)`` with failures mapped to a message."""
    manager = get_manager()
    try:
        text = await manager.run_prompt(
            key=key,
            prompt=prompt,
            cwd=cwd,
            command=command,
            args=args,
            env_overrides=env_overrides,
            auto_approve=auto_approve,
            timeout_seconds=timeout_seconds,
            create_timeout=create_timeout,
            progress_q=progress_q,
            cancel_id=cancel_id,
            mask=mask,
            mcp_config=mcp_config,
        )
        return text, True
    except ImportError:
        return install_hint(), False
    except TimeoutError:
        return f"{agent_label} agent timed out after {timeout_seconds} seconds.", False
    except FileNotFoundError:
        return (
            f"Command '{command}' was not found in PATH. Install Node.js/npx and the "
            f"ACP package for {agent_label}.",
            False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s run failed", agent_label, exc_info=True)
        return classify_turn_error(exc, label=agent_label), False


def cancel_acp_sessions(cancel_id: str) -> int:
    """Stop every live ACP run bound to ``cancel_id``. Safe from any thread."""
    manager = _manager
    if manager is None or not cancel_id:
        return 0
    try:
        future = BackgroundLoop.instance().submit(manager.cancel(cancel_id))
        return int(future.result(timeout=10))
    except Exception:
        logger.debug("acp cancel best-effort failed", exc_info=True)
        return 0


def ask_agent(*, key: str, question: str, timeout_seconds: int) -> str | None:
    """Synchronous wrapper around :meth:`AcpSessionManager.ask`."""
    manager = _manager
    if manager is None or not key:
        return None
    try:
        future = BackgroundLoop.instance().submit(
            manager.ask(key=key, question=question, timeout_seconds=timeout_seconds)
        )
        return future.result(timeout=timeout_seconds + 30)
    except Exception:
        logger.debug("acp ask_agent best-effort failed", exc_info=True)
        return None


def drain_queue(progress_q: queue.Queue, timeout: float) -> list[dict[str, Any]]:
    """Return queued event dicts, blocking up to ``timeout`` for the first one."""
    items: list[dict[str, Any]] = []
    try:
        items.append(
            progress_q.get(timeout=timeout) if timeout else progress_q.get_nowait()
        )
    except queue.Empty:
        return items
    while True:
        try:
            items.append(progress_q.get_nowait())
        except queue.Empty:
            break
    return items
