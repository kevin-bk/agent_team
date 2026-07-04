"""Sandbox abstraction — ABC, errors, ``ExecResult``, and streaming callbacks.

A :class:`Sandbox` is **the** boundary between agent_team's runtime and any host
or container that executes an agent's destructive work (shell, file writes,
builds, tests). Two implementations ship:

* :class:`~.local.LocalSandbox` — host subprocess, **no isolation** (dev/tests).
* :class:`~.opensandbox.OpenSandboxRuntime` — OpenSandbox-backed isolated runtime.

State machine::

                       open()                pause()
        closed  ────────────────►   open  ──────────►  paused
          ▲                          │  ◄──────────       │
          │                          │     resume()       │
          │ close()                  │ close()/error      │ close()
          └──────────────────────────┴────────────────────┘
                          │
                          ▼
                      (broken)         ← on unrecoverable error

Ported from ``deep_agent.sandbox.base`` (stripped of deep-agent-specific
``skills_root`` / ``memory_root`` helpers).
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SandboxError(RuntimeError):
    """Base error from a sandbox runtime."""


class SandboxTimeoutError(SandboxError):
    """Raised when a sandbox command exceeds its wall-clock budget."""


class SandboxAuthError(SandboxError):
    """Authentication / authorization failure — non-recoverable."""


class SandboxRateLimitError(SandboxError):
    """Server-side rate limit — caller may retry after :attr:`retry_after_s`."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class SandboxNotFoundError(SandboxError):
    """The referenced sandbox no longer exists (expired, killed, lost on restart)."""


class SandboxBrokenError(SandboxError):
    """Sandbox is in the ``broken`` state and cannot recover without re-spawning."""


# ---------------------------------------------------------------------------
# ExecResult
# ---------------------------------------------------------------------------


class ExecResult:
    """Result of one command/code execution.

    A plain dataclass-like container (kept dependency-free — no pydantic) so the
    sandbox layer can be imported without pulling in web/API deps.
    """

    __slots__ = ("stdout", "stderr", "exit_code", "duration_ms", "timed_out", "metadata")

    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        duration_ms: int = 0,
        timed_out: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration_ms = duration_ms
        self.timed_out = timed_out
        self.metadata: dict[str, Any] = metadata or {}

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"ExecResult(exit_code={self.exit_code}, timed_out={self.timed_out}, "
            f"stdout={self.stdout[:40]!r}, stderr={self.stderr[:40]!r})"
        )


# ---------------------------------------------------------------------------
# State machine + types
# ---------------------------------------------------------------------------

SandboxState = Literal["closed", "opening", "open", "paused", "broken"]
"""Lifecycle state of a :class:`Sandbox` instance."""

StreamCallback = Callable[[str], "Awaitable[None] | None"]
"""Per-line stdout/stderr callback. May be sync or async; runtime awaits if async."""


# ---------------------------------------------------------------------------
# Sandbox ABC
# ---------------------------------------------------------------------------


class Sandbox(abc.ABC):
    """Abstract sandbox runtime.

    Concrete subclasses must implement :meth:`exec_shell`. All other methods have
    sensible defaults so a minimal runtime (e.g. :class:`LocalSandbox`) doesn't
    have to overload everything. Lifecycle methods are no-ops by default — the
    host process is the implicit "sandbox" for :class:`LocalSandbox`; remote
    runtimes override them.
    """

    #: Identifier (``"local"``, ``"opensandbox"``, …).
    kind: str = "abstract"

    #: True when this sandbox runs in an isolated container/VM (file ops should
    #: be routed through the sandbox instead of the host filesystem).
    is_remote: bool = False

    #: Default workspace root *inside* the sandbox (mount point of the task
    #: workspace). Used to bound path checks and as the default ``cwd``.
    sandbox_workspace_root: str = "/workspace"

    def __init__(self) -> None:
        self.state: SandboxState = "closed"
        self.sandbox_id: str | None = None

    # ─── lifecycle ──────────────────────────────────────────────────────

    async def open(self) -> None:  # noqa: A003 — aligns with __aenter__
        """Provision the sandbox. Idempotent; safe to call multiple times."""
        if self.state == "open":
            return
        self.state = "open"

    async def close(self) -> None:
        """Tear down the sandbox. Idempotent; never raises on double-close."""
        self.state = "closed"
        self.sandbox_id = None

    async def pause(self) -> None:
        """Hibernate the sandbox if supported. Default raises ``NotImplementedError``."""
        raise NotImplementedError(f"{self.kind} sandbox does not support pause()")

    async def resume(self) -> None:
        """Wake a paused sandbox. Default raises ``NotImplementedError``."""
        raise NotImplementedError(f"{self.kind} sandbox does not support resume()")

    async def is_alive(self) -> bool:
        """Return ``True`` while the runtime can still accept work."""
        return self.state == "open"

    # ─── execution ──────────────────────────────────────────────────────

    @abc.abstractmethod
    async def exec_shell(
        self,
        command: str,
        *,
        timeout_seconds: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        on_stdout: StreamCallback | None = None,
        on_stderr: StreamCallback | None = None,
    ) -> ExecResult:
        """Run a shell command; capture stdout+stderr.

        Never raises on non-zero exit — caller inspects :attr:`ExecResult.exit_code`.
        Implementations *should* raise :class:`SandboxTimeoutError` when the
        wall-clock budget is exceeded and the process can't be reaped.

        Streaming callbacks receive newline-terminated chunks **without** the
        trailing newline. They may be sync or async; runtimes await async ones.
        """

    # ─── filesystem ─────────────────────────────────────────────────────

    async def upload(self, local: Path | str, remote_path: str) -> None:
        """Copy a host-side file to ``remote_path`` inside the sandbox."""
        data = Path(local).expanduser().read_bytes()
        await self.upload_bytes(data, remote_path)

    async def upload_bytes(
        self,
        data: bytes,
        remote_path: str,
        *,
        mode: int | None = None,
    ) -> None:
        """Write raw bytes into the sandbox at ``remote_path``.

        Default implementation uses ``base64 | base64 -d`` over shell — works on
        any sandbox with a working ``bash``. Subclasses with a native file API
        should override for efficiency.
        """
        import base64 as _b64

        encoded = _b64.b64encode(data).decode("ascii")
        cmd = (
            f'mkdir -p "$(dirname {_sh_quote(remote_path)})" && '
            f"echo {encoded} | base64 -d > {_sh_quote(remote_path)}"
        )
        if mode is not None:
            cmd += f" && chmod {mode:o} {_sh_quote(remote_path)}"
        res = await self.exec_shell(cmd, timeout_seconds=60)
        if not res.success:
            raise SandboxError(
                f"upload to {remote_path!r} failed (exit={res.exit_code}): {res.stderr[:200]}"
            )

    async def read_text(
        self,
        remote_path: str,
        *,
        encoding: str = "utf-8",
        max_bytes: int = 5_000_000,
    ) -> str:
        """Read ``remote_path`` from the sandbox as text."""
        data = await self.download_bytes(remote_path, max_bytes=max_bytes)
        try:
            return data.decode(encoding, errors="replace")
        except LookupError as e:
            raise SandboxError(f"unknown encoding {encoding!r}") from e

    async def download_bytes(
        self,
        remote_path: str,
        *,
        max_bytes: int = 5_000_000,
    ) -> bytes:
        """Read raw bytes from ``remote_path``. Default uses base64 over shell."""
        import base64 as _b64

        res = await self.exec_shell(
            f"if [ -f {_sh_quote(remote_path)} ]; then "
            f"  if [ $(stat -c%s {_sh_quote(remote_path)} 2>/dev/null "
            f"        || stat -f%z {_sh_quote(remote_path)}) -gt {int(max_bytes)} ]; then "
            f"    echo __TOO_LARGE__ 1>&2; exit 3; "
            f"  fi; "
            f"  base64 < {_sh_quote(remote_path)}; "
            f"else "
            f"  echo __MISSING__ 1>&2; exit 2; "
            f"fi",
            timeout_seconds=60,
        )
        if res.exit_code == 2:
            raise SandboxError(f"file not found: {remote_path!r}")
        if res.exit_code == 3:
            raise SandboxError(f"file too large (>{max_bytes}B): {remote_path!r}")
        if not res.success:
            raise SandboxError(
                f"read of {remote_path!r} failed (exit={res.exit_code}): {res.stderr[:200]}"
            )
        return _b64.b64decode(res.stdout)

    async def write_text(
        self,
        remote_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        mode: int | None = None,
    ) -> None:
        """Write text to ``remote_path``. Convenience over :meth:`upload_bytes`."""
        await self.upload_bytes(content.encode(encoding), remote_path, mode=mode)

    async def path_exists(self, remote_path: str) -> bool:
        """Return True iff ``remote_path`` exists in the sandbox (file or dir)."""
        res = await self.exec_shell(f"test -e {_sh_quote(remote_path)}", timeout_seconds=15)
        return res.success

    # ─── async context manager ──────────────────────────────────────────

    async def __aenter__(self) -> Sandbox:
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        await self.close()

    async def aclose(self) -> None:
        """Alias kept for symmetry with other async-closeable resources."""
        await self.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sh_quote(s: str) -> str:
    """Minimal POSIX single-quote shell escape, for paths/packages."""
    return "'" + s.replace("'", "'\"'\"'") + "'"
