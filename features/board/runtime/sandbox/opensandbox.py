"""OpenSandboxRuntime — :class:`Sandbox` impl on top of the ``opensandbox`` SDK.

Wraps the OpenSandbox Python SDK (``pip install opensandbox``) so the rest of
agent_team stays provider-agnostic. Ported near-verbatim from
``deep_agent.sandbox.opensandbox`` (which is battle-tested against the same
``opensandbox==0.1.9`` this repo pins), including:

* keepalive loop that renews the server-side TTL while in use and **idle-closes**
  the sandbox once untouched past ``idle_timeout`` (so an idle task costs nothing);
* :meth:`pause` / :meth:`resume` / :meth:`resume_existing` for hibernate + restart;
* the ``opensandbox 0.1.9`` UNSET-vs-None Volume-model import fix.

SDK imports are **lazy** so the dependency stays optional — callers using only
:class:`~.local.LocalSandbox` never need it installed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import inspect
import logging
import os
import shlex
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from agent_team.features.board.runtime.sandbox.base import (
    ExecResult,
    Sandbox,
    SandboxAuthError,
    SandboxBrokenError,
    SandboxError,
    SandboxNotFoundError,
    SandboxRateLimitError,
    SandboxTimeoutError,
    StreamCallback,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy SDK accessor
# ---------------------------------------------------------------------------


def _import_sdk() -> dict[str, Any]:
    """Lazy import of the ``opensandbox`` SDK.

    Returns a dict of the names we use so callers do dictionary access instead of
    dotted attribute access — keeps this file forward-compatible with SDK
    refactors. Volume models are imported best-effort (older SDKs lack them).
    """
    try:
        from opensandbox.config import ConnectionConfig  # type: ignore
        from opensandbox.exceptions import SandboxException  # type: ignore
        from opensandbox.models.execd import (  # type: ignore
            ExecutionHandlers,
            RunCommandOpts,
        )
        from opensandbox.sandbox import Sandbox as SdkSandbox  # type: ignore
    except ImportError as e:  # pragma: no cover — depends on env
        raise SandboxError(
            "opensandbox SDK not installed. Install with: pip install opensandbox"
        ) from e

    # CRITICAL (opensandbox 0.1.9): import Volume models from the SDK *domain*
    # layer (``opensandbox.models.sandboxes``), NOT the wire layer
    # (``opensandbox.api.lifecycle.models.*``). The API models default unset
    # backends to an ``UNSET`` sentinel; the converter ``to_api_volume`` checks
    # ``if volume.host is not None`` (True for UNSET) then crashes on ``.path``.
    # The domain models default to ``None`` which is what the converter expects.
    volume_cls: Any | None = None
    host_cls: Any | None = None
    pvc_cls: Any | None = None
    ossfs_cls: Any | None = None
    try:
        from opensandbox.models.sandboxes import (  # type: ignore
            OSSFS as _OSSFS,
        )
        from opensandbox.models.sandboxes import (
            PVC as _PVC,
        )
        from opensandbox.models.sandboxes import (
            Host as _Host,
        )
        from opensandbox.models.sandboxes import (
            Volume as _Volume,
        )

        volume_cls = _Volume
        host_cls = _Host
        pvc_cls = _PVC
        ossfs_cls = _OSSFS
    except ImportError:  # pragma: no cover — old SDK
        try:
            from opensandbox.api.lifecycle.models.host import Host as _Host  # type: ignore
            from opensandbox.api.lifecycle.models.pvc import PVC as _PVC  # type: ignore
            from opensandbox.api.lifecycle.models.volume import (  # type: ignore
                Volume as _Volume,
            )

            try:
                from opensandbox.api.lifecycle.models.ossfs import OSSFS as _OSSFS  # type: ignore
            except ImportError:
                _OSSFS = None  # type: ignore[assignment]

            volume_cls = _Volume
            host_cls = _Host
            pvc_cls = _PVC
            ossfs_cls = _OSSFS
        except ImportError:
            pass

    return {
        "ConnectionConfig": ConnectionConfig,
        "SandboxException": SandboxException,
        "ExecutionHandlers": ExecutionHandlers,
        "RunCommandOpts": RunCommandOpts,
        "SdkSandbox": SdkSandbox,
        "Volume": volume_cls,
        "Host": host_cls,
        "PVC": pvc_cls,
        "OSSFS": ossfs_cls,
    }


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class OpenSandboxRuntime(Sandbox):
    """Sandbox runtime backed by the OpenSandbox service.

    Construction is cheap — the container spawns only on :meth:`open`. Prefer the
    async-context-manager pattern; a paused sandbox can be reattached via
    :meth:`resume_existing`.
    """

    kind = "opensandbox"
    is_remote = True

    def __init__(
        self,
        *,
        server_url: str = "http://localhost:8090",
        api_key: str | None = None,
        image: str = "opensandbox/code-interpreter:v1.0.2.clone3.fix1",
        timeout: timedelta = timedelta(hours=8),
        idle_timeout: timedelta | None = None,
        renew_interval_seconds: float | None = None,
        env: dict[str, str] | None = None,
        env_blocklist: list[str] | None = None,
        mounts: list[Any] | None = None,
        metadata: dict[str, str] | None = None,
        cpu: float = 2.0,
        memory_mb: int = 1024,
        ready_timeout_seconds: int = 60,
        name: str | None = None,
        request_timeout_seconds: int = 600,
        use_server_proxy: bool = True,
        workspace_mount_path: str = "/workspace",
    ) -> None:
        super().__init__()
        self.server_url = server_url
        self.api_key = (
            api_key
            or os.environ.get("OPENSANDBOX_API_KEY")
            or os.environ.get("OPEN_SANDBOX_API_KEY")
        )
        self.image = image
        self.timeout = timeout
        self.idle_timeout = idle_timeout
        self._renew_interval_override = renew_interval_seconds
        self._last_activity = time.monotonic()
        self._keepalive_task: asyncio.Task | None = None
        self.env_blocklist: list[str] = list(env_blocklist or [])
        self.env = _apply_env_blocklist(dict(env or {}), self.env_blocklist)
        self.mounts: list[Any] = list(mounts or [])
        self.metadata = dict(metadata or {})
        self.cpu = cpu
        self.memory_mb = memory_mb
        self.ready_timeout_seconds = ready_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.use_server_proxy = use_server_proxy
        self.name = name
        self.sandbox_workspace_root = workspace_mount_path

        self._sdk: dict[str, Any] | None = None
        self._sdk_sandbox: Any | None = None
        self._connection_config: Any | None = None

    # ─── helpers ────────────────────────────────────────────────────────

    def _ensure_sdk(self) -> dict[str, Any]:
        if self._sdk is None:
            self._sdk = _import_sdk()
        return self._sdk

    def _build_connection_config(self) -> Any:
        if self._connection_config is not None:
            return self._connection_config
        sdk = self._ensure_sdk()

        domain = self.server_url
        protocol = "http"
        if "://" in domain:
            protocol, _, domain = domain.partition("://")
        cfg_cls = sdk["ConnectionConfig"]
        kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "domain": domain,
            "protocol": protocol,
            "request_timeout": timedelta(seconds=self.request_timeout_seconds),
        }
        try:
            cfg_params = inspect.signature(cfg_cls).parameters
        except (TypeError, ValueError):
            cfg_params = {}
        if "use_server_proxy" in cfg_params:
            kwargs["use_server_proxy"] = self.use_server_proxy
        self._connection_config = cfg_cls(**kwargs)
        return self._connection_config

    def _resource_dict(self) -> dict[str, str]:
        return {"cpu": str(self.cpu), "memory": f"{self.memory_mb}Mi"}

    # ─── lifecycle ──────────────────────────────────────────────────────

    async def open(self) -> None:  # noqa: A003
        if self.state == "open":
            return
        sdk = self._ensure_sdk()
        self.state = "opening"

        metadata = dict(self.metadata)
        if self.name:
            metadata.setdefault("name", self.name)

        volumes = _build_volumes(self.mounts, sdk=sdk) if self.mounts else None

        create_kwargs: dict[str, Any] = {
            "connection_config": self._build_connection_config(),
            "timeout": self.timeout,
            "resource": self._resource_dict(),
            "env": self.env or None,
            "metadata": metadata or None,
            "ready_timeout": timedelta(seconds=self.ready_timeout_seconds),
        }
        if volumes is not None:
            create_kwargs["volumes"] = volumes

        try:
            self._sdk_sandbox = await sdk["SdkSandbox"].create(self.image, **create_kwargs)
        except Exception as e:  # noqa: BLE001
            self.state = "broken"
            raise _map_exception(e, sdk=sdk) from e

        self.sandbox_id = _get_sandbox_id(self._sdk_sandbox)
        self.state = "open"
        self._last_activity = time.monotonic()
        self._start_keepalive()

    # ─── keepalive / idle management ─────────────────────────────────────

    def _renew_interval_seconds(self) -> float:
        if self._renew_interval_override is not None:
            return self._renew_interval_override
        window = self.timeout.total_seconds()
        return max(60.0, min(window / 3.0, 1800.0))

    def _start_keepalive(self) -> None:
        if self._keepalive_task is not None and not self._keepalive_task.done():
            return
        with contextlib.suppress(RuntimeError):  # no running loop (sync tests)
            self._keepalive_task = asyncio.create_task(
                self._keepalive_loop(), name=f"sandbox-keepalive-{self.sandbox_id}"
            )

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    async def _keepalive_loop(self) -> None:
        """Renew the server-side TTL until idle past ``idle_timeout``.

        OpenSandbox has no idle timeout of its own and its ``timeout`` is an
        *absolute* expiry, so without renewal the sandbox dies on a wall-clock
        deadline even while in use.
        """
        interval = self._renew_interval_seconds()
        try:
            while True:
                await asyncio.sleep(interval)
                if self.state != "open" or self._sdk_sandbox is None:
                    return
                if self.idle_timeout is not None:
                    idle = time.monotonic() - self._last_activity
                    if idle >= self.idle_timeout.total_seconds():
                        logger.info(
                            "sandbox %s idle %.0fs ≥ %.0fs — closing",
                            self.sandbox_id,
                            idle,
                            self.idle_timeout.total_seconds(),
                        )
                        await self._kill_sdk()
                        self.state = "closed"
                        self.sandbox_id = None
                        return
                try:
                    await self.renew(self.timeout)
                except SandboxError as exc:
                    logger.warning("sandbox %s renew failed: %s", self.sandbox_id, exc)
        except asyncio.CancelledError:
            raise

    async def renew(self, window: timedelta | None = None) -> None:
        """Push the server-side expiry to ``now + window`` (default :attr:`timeout`)."""
        if self.state != "open" or self._sdk_sandbox is None:
            return
        sdk = self._ensure_sdk()
        try:
            await self._sdk_sandbox.renew(window or self.timeout)
        except Exception as e:
            raise _map_exception(e, sdk=sdk) from e

    async def resume_existing(self, sandbox_id: str) -> None:
        """Reattach to a previously created (possibly paused) sandbox by id.

        Used to survive a process restart: replay the persisted ``sandbox_id`` and
        the same container resumes.
        """
        sdk = self._ensure_sdk()
        self.state = "opening"
        try:
            self._sdk_sandbox = await sdk["SdkSandbox"].resume(
                sandbox_id=sandbox_id,
                connection_config=self._build_connection_config(),
            )
        except Exception as e:  # noqa: BLE001
            self.state = "broken"
            raise _map_exception(e, sdk=sdk) from e

        self.sandbox_id = sandbox_id
        self.state = "open"
        self._last_activity = time.monotonic()
        self._start_keepalive()

    async def pause(self) -> None:
        if self.state != "open":
            return
        sdk = self._ensure_sdk()
        try:
            await self._sdk_sandbox.pause()  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            raise _map_exception(e, sdk=sdk) from e
        self.state = "paused"

    async def resume(self) -> None:
        if self.state == "open":
            return
        if self.sandbox_id is None:
            raise SandboxError(
                "resume() requires a sandbox_id (call resume_existing instead)"
            )
        await self.resume_existing(self.sandbox_id)

    async def close(self) -> None:
        task = self._keepalive_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
        self._keepalive_task = None
        await self._kill_sdk()
        self.state = "closed"
        self.sandbox_id = None

    async def _kill_sdk(self) -> None:
        """Kill the remote container + close the SDK client. Never raises."""
        if self._sdk_sandbox is None:
            return
        sdk = self._ensure_sdk()
        try:
            await self._sdk_sandbox.kill()
        except Exception as e:  # noqa: BLE001 — never raise from cleanup
            _ = _map_exception(e, sdk=sdk)
        finally:
            try:
                close = getattr(self._sdk_sandbox, "close", None)
                if close is not None:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            except Exception:  # noqa: BLE001
                pass
            self._sdk_sandbox = None

    async def is_alive(self) -> bool:
        if self.state != "open" or self._sdk_sandbox is None:
            return False
        try:
            info = await self._sdk_sandbox.get_info()
        except Exception:  # noqa: BLE001
            return False
        state = getattr(getattr(info, "status", None), "state", None) or getattr(
            info, "state", None
        )
        return state in (None, "RUNNING", "READY", "running", "ready")

    async def get_endpoint(self, port: int) -> Any:
        """Resolve the public proxy endpoint for an in-sandbox ``port``."""
        if self.state != "open" or self._sdk_sandbox is None:
            raise SandboxBrokenError(
                f"sandbox not open (state={self.state}); call open() first"
            )
        return await self._sdk_sandbox.get_endpoint(port)

    # ─── execution ──────────────────────────────────────────────────────

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
        if self.state != "open" or self._sdk_sandbox is None:
            raise SandboxBrokenError(
                f"sandbox not open (state={self.state}); call open() first"
            )

        self._touch()
        sdk = self._ensure_sdk()

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        async def _on_stdout(msg: Any) -> None:
            text = getattr(msg, "text", "") or ""
            stdout_parts.append(text)
            if on_stdout is not None:
                ret = on_stdout(text.rstrip("\n"))
                if inspect.isawaitable(ret):
                    await ret

        async def _on_stderr(msg: Any) -> None:
            text = getattr(msg, "text", "") or ""
            stderr_parts.append(text)
            if on_stderr is not None:
                ret = on_stderr(text.rstrip("\n"))
                if inspect.isawaitable(ret):
                    await ret

        handlers = sdk["ExecutionHandlers"](on_stdout=_on_stdout, on_stderr=_on_stderr)
        # ``timeout`` belongs INSIDE RunCommandOpts, not as a direct kwarg.
        opts: dict[str, Any] = {}
        if cwd is not None:
            opts["working_directory"] = cwd
        if env:
            opts["envs"] = env
        if timeout_seconds is not None:
            opts["timeout"] = timedelta(seconds=timeout_seconds)

        started_at = time.time()
        timed_out = False
        execution: Any
        try:
            run_opts = sdk["RunCommandOpts"](**opts) if opts else None
            kwargs: dict[str, Any] = {"handlers": handlers}
            if run_opts is not None:
                kwargs["opts"] = run_opts
            execution = await self._sdk_sandbox.commands.run(command, **kwargs)
        except Exception as e:  # noqa: BLE001
            mapped = _map_exception(e, sdk=sdk)
            if isinstance(mapped, SandboxTimeoutError):
                timed_out = True
                execution = None
            else:
                raise mapped from e

        duration_ms = int((time.time() - started_at) * 1000)
        exit_code = -1
        if execution is not None:
            exit_code = (
                getattr(execution, "exit_code", None)
                or getattr(execution, "exitCode", None)
                or 0
            )

        return ExecResult(
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            exit_code=int(exit_code),
            duration_ms=duration_ms,
            timed_out=timed_out,
            metadata={"sandbox_id": self.sandbox_id, "kind": self.kind},
        )

    # ─── filesystem ─────────────────────────────────────────────────────

    async def upload_bytes(
        self,
        data: bytes,
        remote_path: str,
        *,
        mode: int | None = None,
    ) -> None:
        if self.state != "open" or self._sdk_sandbox is None:
            raise SandboxBrokenError("sandbox not open; call open() first")
        self._touch()
        sdk = self._ensure_sdk()
        try:
            from opensandbox.models.filesystem import WriteEntry  # type: ignore
        except ImportError as e:
            raise SandboxError("opensandbox SDK missing filesystem models") from e
        try:
            await self._sdk_sandbox.files.write_files([WriteEntry(path=remote_path, data=data)])
        except Exception as e:  # noqa: BLE001
            raise _map_exception(e, sdk=sdk) from e
        if mode is not None:
            res = await self.exec_shell(f"chmod {mode:o} {remote_path!r}")
            if not res.success:
                raise SandboxError(
                    f"chmod {mode:o} {remote_path!r} failed "
                    f"(exit={res.exit_code}): {res.stderr[:200]}"
                )

    async def download_bytes(
        self,
        remote_path: str,
        *,
        max_bytes: int = 5_000_000,
    ) -> bytes:
        if self.state != "open" or self._sdk_sandbox is None:
            raise SandboxBrokenError("sandbox not open; call open() first")
        self._touch()
        sdk = self._ensure_sdk()
        try:
            content = await self._sdk_sandbox.files.read_file(remote_path)
            data = content if isinstance(content, bytes) else str(content).encode("utf-8")
        except Exception as e:  # noqa: BLE001
            # ``read_file`` is text-only; fall back to a binary-safe base64 read.
            try:
                data = await self._download_bytes_via_base64(remote_path)
            except SandboxError:
                raise
            except Exception:
                raise _map_exception(e, sdk=sdk) from e
        if len(data) > max_bytes:
            raise SandboxError(f"file too large (>{max_bytes}B): {remote_path!r}")
        return data

    async def _download_bytes_via_base64(self, remote_path: str) -> bytes:
        res = await self.exec_shell(f"base64 {shlex.quote(remote_path)}")
        if not res.success:
            raise SandboxError(
                f"binary download failed for {remote_path!r} "
                f"(exit={res.exit_code}): {res.stderr[:200]}"
            )
        try:
            return base64.b64decode(res.stdout)
        except ValueError as e:
            raise SandboxError(f"base64 decode failed for {remote_path!r}") from e

    async def upload(self, local: Path | str, remote_path: str) -> None:
        data = Path(local).expanduser().read_bytes()
        await self.upload_bytes(data, remote_path)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _apply_env_blocklist(env: dict[str, str], blocklist: list[str]) -> dict[str, str]:
    """Return a shallow copy of *env* with every key in *blocklist* dropped."""
    if not blocklist:
        return env
    blocked = set(blocklist)
    return {k: v for k, v in env.items() if k not in blocked}


def _build_volumes(mounts: list[Any], *, sdk: dict[str, Any]) -> list[Any]:
    """Translate :class:`VolumeMount` (or duck-typed twin) into SDK ``Volume`` list."""
    volume_cls = sdk.get("Volume")
    host_cls = sdk.get("Host")
    pvc_cls = sdk.get("PVC")
    ossfs_cls = sdk.get("OSSFS")

    if volume_cls is None or host_cls is None or pvc_cls is None:
        raise SandboxError(
            "Volume mounts requested but the installed opensandbox SDK does not "
            "expose the Volume models. Upgrade to opensandbox>=0.1.5."
        )

    out: list[Any] = []
    for m in mounts:
        kind = getattr(m, "kind", None)
        name = getattr(m, "name", None)
        mount_path = getattr(m, "mount_path", None)
        if not (kind and name and mount_path):
            raise SandboxError(
                f"VolumeMount {name!r}: missing required field (kind / name / mount_path)"
            )

        backend_kwargs: dict[str, Any] = {
            "name": name,
            "mount_path": mount_path,
            "read_only": bool(getattr(m, "read_only", False)),
        }
        sub_path = getattr(m, "sub_path", None)
        if sub_path:
            backend_kwargs["sub_path"] = sub_path

        if kind == "host":
            host_path = getattr(m, "host_path", None)
            if not host_path:
                raise SandboxError(f"VolumeMount {name!r}: kind=host but host_path is empty")
            backend_kwargs["host"] = host_cls(path=host_path)
        elif kind == "pvc":
            claim_name = getattr(m, "pvc_claim", None)
            if not claim_name:
                raise SandboxError(f"VolumeMount {name!r}: kind=pvc but pvc_claim is empty")
            pvc_kwargs: dict[str, Any] = {"claim_name": claim_name}
            for src_attr, dst_attr in (
                ("pvc_create_if_not_exists", "create_if_not_exists"),
                ("pvc_delete_on_sandbox_termination", "delete_on_sandbox_termination"),
                ("pvc_storage_class", "storage_class"),
                ("pvc_storage", "storage"),
                ("pvc_access_modes", "access_modes"),
            ):
                val = getattr(m, src_attr, None)
                if val is not None:
                    pvc_kwargs[dst_attr] = val
            try:
                backend_kwargs["pvc"] = pvc_cls(**pvc_kwargs)
            except TypeError:
                backend_kwargs["pvc"] = pvc_cls(claim_name=claim_name)
        elif kind == "ossfs":
            if ossfs_cls is None:
                raise SandboxError(
                    f"VolumeMount {name!r}: kind=ossfs but installed opensandbox SDK "
                    "does not expose the OSSFS model"
                )
            uri = getattr(m, "ossfs_uri", None)
            if not uri:
                raise SandboxError(f"VolumeMount {name!r}: kind=ossfs but ossfs_uri is empty")
            backend_kwargs["ossfs"] = ossfs_cls(uri=uri)
        else:
            raise SandboxError(f"VolumeMount {name!r}: unknown kind={kind!r}")

        out.append(volume_cls(**backend_kwargs))

    return out


def _get_sandbox_id(sb: Any) -> str | None:
    for attr in ("id", "sandbox_id", "sandboxId"):
        val = getattr(sb, attr, None)
        if val:
            return str(val)
    info = getattr(sb, "info", None)
    if info is not None:
        for attr in ("id", "sandbox_id", "sandboxId"):
            val = getattr(info, attr, None)
            if val:
                return str(val)
    return None


def _map_exception(e: BaseException, *, sdk: dict[str, Any]) -> SandboxError:
    """Translate an ``opensandbox.SandboxException`` (or other) into our hierarchy."""
    sbx_exc_cls = sdk.get("SandboxException")
    if sbx_exc_cls is not None and isinstance(e, sbx_exc_cls):  # type: ignore[arg-type]
        code = str(getattr(getattr(e, "error", None), "code", "") or "").upper()
        message = str(getattr(getattr(e, "error", None), "message", "") or str(e))
        if "AUTH" in code or "FORBIDDEN" in code or "UNAUTHORIZED" in code:
            return SandboxAuthError(message)
        if "RATE" in code or "TOO_MANY" in code or "QUOTA" in code:
            return SandboxRateLimitError(message)
        if "NOT_FOUND" in code or "MISSING" in code:
            return SandboxNotFoundError(message)
        if "TIMEOUT" in code or "DEADLINE" in code:
            return SandboxTimeoutError(message)
        return SandboxError(message)

    name = e.__class__.__name__.lower()
    if "timeout" in name or "deadline" in name:
        return SandboxTimeoutError(str(e))
    if "auth" in name or "forbidden" in name:
        return SandboxAuthError(str(e))
    if "rate" in name or "limit" in name:
        return SandboxRateLimitError(str(e))
    if "notfound" in name or "missing" in name:
        return SandboxNotFoundError(str(e))
    return SandboxError(str(e))
