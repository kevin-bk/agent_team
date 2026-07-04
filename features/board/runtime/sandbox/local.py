"""LocalSandbox — runs subprocesses on the host with NO isolation.

For trusted dev environments and tests only. Streams stdout/stderr and enforces
``timeout_seconds`` via ``asyncio.wait_for``; on timeout the whole process group
is killed to prevent zombies. Ported from ``deep_agent.sandbox.local``.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import signal
import sys
import time
from pathlib import Path

from agent_team.features.board.runtime.sandbox.base import (
    ExecResult,
    Sandbox,
    SandboxError,
    StreamCallback,
)


class LocalSandbox(Sandbox):
    """Run commands on the host (no isolation; dev/tests)."""

    kind = "local"
    is_remote = False

    def __init__(self, workspace_root: str | Path) -> None:
        super().__init__()
        self.workspace_root = Path(workspace_root).expanduser()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        # The host workspace *is* the writable root; point the mount property at it.
        self.sandbox_workspace_root = str(self.workspace_root)
        self.state = "open"
        self.sandbox_id = f"local:{self.workspace_root}"

    async def upload_bytes(  # noqa: PLR6301 — signature must match base
        self,
        data: bytes,
        remote_path: str,
        *,
        mode: int | None = None,
    ) -> None:
        """Write bytes straight to the host filesystem (no base64 shell round-trip)."""
        target = Path(remote_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if mode is not None:
            target.chmod(mode)

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
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        working_dir = str(Path(cwd).expanduser()) if cwd else str(self.workspace_root)

        started_at = time.time()

        kw: dict = {}
        if not sys.platform.startswith("win"):
            kw["preexec_fn"] = os.setsid

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=working_dir,
            env=merged_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kw,
        )

        timed_out = False
        if on_stdout or on_stderr:
            stdout_b, stderr_b, timed_out = await _drain_with_callbacks(
                proc, on_stdout, on_stderr, timeout_seconds
            )
        else:
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds
                )
            except TimeoutError:
                _kill_tree(proc)
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=2.0
                    )
                except TimeoutError:
                    stdout_b, stderr_b = b"", b""
                timed_out = True

        duration_ms = int((time.time() - started_at) * 1000)
        return ExecResult(
            stdout=_decode(stdout_b),
            stderr=_decode(stderr_b),
            exit_code=proc.returncode if proc.returncode is not None else -1,
            duration_ms=duration_ms,
            timed_out=timed_out,
            metadata={"cwd": working_dir, "kind": "local"},
        )


async def _drain_with_callbacks(
    proc: asyncio.subprocess.Process,
    on_stdout: StreamCallback | None,
    on_stderr: StreamCallback | None,
    timeout_seconds: float | None,
) -> tuple[bytes, bytes, bool]:
    """Read stdout/stderr line-by-line, invoking callbacks; return buffers + timeout flag."""

    async def _pump(
        stream: asyncio.StreamReader | None,
        cb: StreamCallback | None,
        buf: bytearray,
    ) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.readline()
            if not chunk:
                return
            buf.extend(chunk)
            if cb is not None:
                line = chunk.decode("utf-8", errors="replace").rstrip("\n")
                ret = cb(line)
                if inspect.isawaitable(ret):
                    await ret

    stdout_buf = bytearray()
    stderr_buf = bytearray()

    pumps = asyncio.gather(
        _pump(proc.stdout, on_stdout, stdout_buf),
        _pump(proc.stderr, on_stderr, stderr_buf),
    )

    timed_out = False
    try:
        await asyncio.wait_for(pumps, timeout=timeout_seconds)
        await proc.wait()
    except TimeoutError:
        timed_out = True
        _kill_tree(proc)
        pumps.cancel()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except TimeoutError:
            pass

    return bytes(stdout_buf), bytes(stderr_buf), timed_out


def _decode(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("utf-8", errors="replace")


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Best-effort kill of the entire process tree spawned by ``proc``."""
    if proc.returncode is not None:
        return
    pid = proc.pid
    try:
        if sys.platform.startswith("win"):  # pragma: no cover
            proc.kill()
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        time.sleep(0.5)
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            return
    except Exception as e:  # noqa: BLE001
        raise SandboxError(f"failed to kill subprocess: {e}") from e
