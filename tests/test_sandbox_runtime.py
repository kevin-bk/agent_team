"""Unit tests for the isolated OpenSandbox runtime (no live server / SDK).

Covers the ported sandbox layer and the sandboxed CLI worker using fakes:

* config resolution from env
* LocalSandbox exec + file round-trip
* OpenSandboxRuntime lifecycle against a fake SDK (open/exec/pause/resume/close)
* volume translation
* SandboxManager per-task tracking + idle GC reap
* Claude stream-json → AgentEvent frame translation
* SandboxedCliWorker: emits frames, pauses after the turn, and (strict mode)
  never falls back to the host when the sandbox is unavailable
"""

from __future__ import annotations

import asyncio

import pytest
from agent_team.features.board.runtime.sandbox import cli_exec
from agent_team.features.board.runtime.sandbox import opensandbox as osb
from agent_team.features.board.runtime.sandbox.base import (
    ExecResult,
    Sandbox,
    SandboxError,
)
from agent_team.features.board.runtime.sandbox.config import (
    RuntimeProfile,
    VolumeMount,
    profile_from_env,
    validate_overlay,
)
from agent_team.features.board.runtime.sandbox.local import LocalSandbox
from agent_team.features.board.runtime.sandbox.manager import SandboxManager

# ─── config ──────────────────────────────────────────────────────────────


def test_profile_from_env_defaults_to_local(monkeypatch):
    for key in list(_RUNTIME_ENV):
        monkeypatch.delenv(key, raising=False)
    prof = profile_from_env()
    assert prof.provider == "local"
    assert not prof.is_sandboxed


def test_profile_from_env_opensandbox(monkeypatch):
    monkeypatch.setenv("AGENT_TEAM_RUNTIME_PROVIDER", "opensandbox")
    monkeypatch.setenv("AGENT_TEAM_RUNTIME_IMAGE", "agent-team/agent-team-sandbox:test")
    monkeypatch.setenv("AGENT_TEAM_RUNTIME_IDLE_MINUTES", "5")
    monkeypatch.setenv("AGENT_TEAM_RUNTIME_STRICT", "1")
    prof = profile_from_env()
    assert prof.is_sandboxed
    assert prof.image == "agent-team/agent-team-sandbox:test"
    assert prof.idle_timeout_minutes == 5
    assert prof.strict_isolation is True


_RUNTIME_ENV = [
    "AGENT_TEAM_RUNTIME_PROVIDER",
    "AGENT_TEAM_RUNTIME_IMAGE",
    "AGENT_TEAM_RUNTIME_IDLE_MINUTES",
    "AGENT_TEAM_RUNTIME_STRICT",
]


# ─── board runtime overlay validation ──────────────────────────────────────


def test_validate_overlay_accepts_known_fields():
    cleaned, err = validate_overlay(
        {
            "provider": "opensandbox",
            "runtime_strategy": "acp_sidecar",
            "image": "myuser/agent-team-sandbox:v1",
            "cpu": 4,
            "memory_mb": 8192,
            "idle_timeout_minutes": 15,
            "workspace_mode": "mount",
            "strict_isolation": True,
        }
    )
    assert err is None
    assert cleaned["provider"] == "opensandbox"
    assert cleaned["runtime_strategy"] == "acp_sidecar"
    assert cleaned["cpu"] == 4
    assert cleaned["strict_isolation"] is True


def test_validate_overlay_drops_none_and_keeps_partial():
    cleaned, err = validate_overlay({"image": "x:1", "cpu": None})
    assert err is None
    assert cleaned == {"image": "x:1"}


def test_validate_overlay_rejects_unknown_field():
    cleaned, err = validate_overlay({"server_url": "http://evil"})
    assert cleaned == {}
    assert err is not None and "unknown" in err


def test_validate_overlay_rejects_bad_enum():
    _, err = validate_overlay({"provider": "aws"})
    assert err is not None and "provider" in err


def test_validate_overlay_rejects_bad_types():
    _, err = validate_overlay({"cpu": -1})
    assert err is not None
    _, err = validate_overlay({"memory_mb": "lots"})
    assert err is not None
    _, err = validate_overlay({"strict_isolation": "yes"})
    assert err is not None
    # booleans must not sneak through the numeric branch
    _, err = validate_overlay({"memory_mb": True})
    assert err is not None


# ─── ACP session store — standalone SQLite backend (in-sandbox) ─────────────


def test_acp_store_sqlite_backend_roundtrip(tmp_path):
    """The stdlib-sqlite3 backend round-trips without importing core/plugins."""
    from agent_team.features.board.runtime.acp import store

    db = tmp_path / "acp.db"
    orig_path, orig_ready = store._SQLITE_PATH, store._sqlite_ready
    store._SQLITE_PATH = str(db)
    store._sqlite_ready = False
    try:
        assert store.load_session_id("k::t") is None
        store.save_session_id("k::t", "sid-1", "/workspace")
        assert store.load_session_id("k::t") == ("sid-1", "/workspace")
        # upsert keeps a single row per key
        store.save_session_id("k::t", "sid-2", "/w2")
        assert store.load_session_id("k::t") == ("sid-2", "/w2")
        store.delete_session_id("k::t")
        assert store.load_session_id("k::t") is None
    finally:
        store._SQLITE_PATH, store._sqlite_ready = orig_path, orig_ready


# ─── LocalSandbox ────────────────────────────────────────────────────────


async def test_local_sandbox_exec_and_file_roundtrip(tmp_path):
    sb = LocalSandbox(workspace_root=tmp_path)
    res = await sb.exec_shell("echo hello")
    assert res.success
    assert "hello" in res.stdout

    await sb.write_text(str(tmp_path / "f.txt"), "content-123")
    back = await sb.read_text(str(tmp_path / "f.txt"))
    assert back == "content-123"


async def test_local_sandbox_streams_stdout(tmp_path):
    sb = LocalSandbox(workspace_root=tmp_path)
    lines: list[str] = []
    res = await sb.exec_shell("printf 'a\\nb\\nc\\n'", on_stdout=lambda ln: lines.append(ln))
    assert res.success
    assert lines == ["a", "b", "c"]


# ─── OpenSandboxRuntime against a fake SDK ───────────────────────────────


class _FakeExecution:
    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code


class _FakeCommands:
    def __init__(self, sandbox: _FakeSdkSandbox) -> None:
        self._sb = sandbox

    async def run(self, command, *, handlers=None, opts=None):
        self._sb.commands_run.append(command)
        if handlers is not None and getattr(handlers, "on_stdout", None):
            await handlers.on_stdout(_Msg("line-1\n"))
            await handlers.on_stdout(_Msg("line-2\n"))
        return _FakeExecution(exit_code=0)


class _Msg:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeCredentialVault:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create(self, *, credentials, bindings):
        self.created.append({"credentials": credentials, "bindings": bindings})
        return {"revision": 1}


class _FakeSdkSandbox:
    created: list[str] = []
    last_create_kwargs: dict = {}

    def __init__(self, image: str, sandbox_id: str = "sbx-1") -> None:
        self.image = image
        self.id = sandbox_id
        self.commands = _FakeCommands(self)
        self.commands_run: list[str] = []
        self.paused = False
        self.killed = False
        self.renewed = 0
        self.credential_vault = _FakeCredentialVault()

    @classmethod
    async def create(cls, image, **kwargs):
        inst = cls(image)
        cls.created.append(image)
        cls.last_create_kwargs = kwargs
        return inst

    @classmethod
    async def resume(cls, *, sandbox_id, connection_config=None):
        inst = cls("resumed", sandbox_id=sandbox_id)
        return inst

    async def pause(self):
        self.paused = True

    async def renew(self, window):
        self.renewed += 1

    async def kill(self):
        self.killed = True

    async def get_info(self):
        class _I:
            state = "RUNNING"

        return _I()


class _FakeVolume:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeHost:
    def __init__(self, path):
        self.path = path


def _fake_sdk() -> dict:
    class _Handlers:
        def __init__(self, on_stdout=None, on_stderr=None):
            self.on_stdout = on_stdout
            self.on_stderr = on_stderr

    class _Opts:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _ConnCfg:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Exc(Exception):
        pass

    class _NetPolicy:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _NetRule:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _CredProxy:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    return {
        "ConnectionConfig": _ConnCfg,
        "SandboxException": _Exc,
        "ExecutionHandlers": _Handlers,
        "RunCommandOpts": _Opts,
        "SdkSandbox": _FakeSdkSandbox,
        "Volume": _FakeVolume,
        "Host": _FakeHost,
        "PVC": _FakeVolume,
        "OSSFS": _FakeVolume,
        "NetworkPolicy": _NetPolicy,
        "NetworkRule": _NetRule,
        "CredentialProxyConfig": _CredProxy,
    }


async def test_opensandbox_lifecycle(monkeypatch):
    monkeypatch.setattr(osb, "_import_sdk", _fake_sdk)
    rt = osb.OpenSandboxRuntime(
        image="agent-team/runtime:test",
        renew_interval_seconds=3600,  # keepalive won't fire during the test
    )
    await rt.open()
    assert rt.state == "open"
    assert rt.sandbox_id == "sbx-1"

    got_lines: list[str] = []
    res = await rt.exec_shell("echo hi", on_stdout=lambda ln: got_lines.append(ln))
    assert res.success
    assert got_lines == ["line-1", "line-2"]

    await rt.pause()
    assert rt.state == "paused"

    await rt.resume()
    assert rt.state == "open"

    await rt.close()
    assert rt.state == "closed"


async def test_opensandbox_build_volumes(monkeypatch):
    sdk = _fake_sdk()
    mount = VolumeMount(
        name="task-workspace",
        kind="host",
        host_path="/host/ws",
        mount_path="/workspace",
    )
    volumes = osb._build_volumes([mount], sdk=sdk)
    assert len(volumes) == 1
    assert volumes[0].kwargs["mount_path"] == "/workspace"
    assert volumes[0].kwargs["host"].path == "/host/ws"


# ─── SandboxManager ──────────────────────────────────────────────────────


async def test_manager_per_task_and_idle_gc(tmp_path):
    prof = RuntimeProfile(provider="local")
    mgr = SandboxManager(profile=prof, idle_ttl_seconds=0.01, gc_interval_seconds=0)

    sb = await mgr.open_for_task("TASK-1", workspace_root=str(tmp_path))
    assert isinstance(sb, Sandbox)
    assert mgr.has_task("TASK-1")
    assert mgr.open_count == 1

    # Opening the same task twice is a bug and refused.
    with pytest.raises(ValueError):
        await mgr.open_for_task("TASK-1", workspace_root=str(tmp_path))

    # Idle sweep reaps the untouched sandbox.
    await asyncio.sleep(0.02)
    reaped = await mgr.sweep_idle()
    assert reaped == ["TASK-1"]
    assert not mgr.has_task("TASK-1")


async def test_manager_pin_prevents_reap(tmp_path):
    import time as _time

    prof = RuntimeProfile(provider="local")
    mgr = SandboxManager(profile=prof, idle_ttl_seconds=0.01, gc_interval_seconds=0)
    await mgr.open_for_task("TASK-2", workspace_root=str(tmp_path))
    mgr.pin_until("TASK-2", _time.time() + 3600)
    await asyncio.sleep(0.02)
    assert await mgr.sweep_idle() == []
    assert mgr.has_task("TASK-2")
    await mgr.aclose()


# ─── Claude stream-json translation ──────────────────────────────────────


def test_claude_translator_maps_stream():
    spec = cli_exec.get_exec_spec("claude")
    t = spec.new_translator()
    frames: list[tuple[str, dict]] = []
    lines = [
        '{"type":"system","subtype":"init"}',
        '{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"Hel"}}}',
        '{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"lo"}}}',
        '{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ls"}}',
        '{"type":"tool_result","tool_use_id":"t1","content":"a.py"}',
        '{"type":"message_delta","usage":{"input_tokens":10,"output_tokens":5}}',
        '{"type":"result","subtype":"success","result":"Done.","total_cost_usd":0.01}',
    ]
    for line in lines:
        frames.extend(t.on_line(line))
    frames.extend(t.finalize())

    kinds = [f[0] for f in frames]
    assert "text_delta" in kinds
    assert "tool_use_start" in kinds
    assert "tool_use_end" in kinds
    assert "usage" in kinds
    assert t.result.final_text == "Done."
    assert t.result.usage["input_tokens"] == 10
    assert t.result.ok is True


def test_claude_build_argv():
    spec = cli_exec.get_exec_spec("claude")
    argv = spec.build_argv(prompt="do it", workdir="/workspace")
    assert argv[0] == "claude"
    assert "-p" in argv and "do it" in argv
    assert "stream-json" in argv


def test_unsupported_engine_raises():
    with pytest.raises(NotImplementedError):
        cli_exec.get_exec_spec("gemini")


def test_codex_translator_maps_stream():
    spec = cli_exec.get_exec_spec("codex")
    t = spec.new_translator()
    frames: list[tuple[str, dict]] = []
    lines = [
        '{"type":"thread.started","thread_id":"th"}',
        '{"type":"turn.started"}',
        '{"type":"item.started","item":{"id":"i1","type":"command_execution","command":"ls"}}',
        '{"type":"item.completed","item":{"id":"i1","type":"command_execution","exit_code":0,"aggregated_output":"a.py"}}',  # noqa: E501
        '{"type":"item.completed","item":{"id":"i3","type":"agent_message","text":"Repo scanned."}}',  # noqa: E501
        '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20,"cached_input_tokens":50}}',
    ]
    for line in lines:
        frames.extend(t.on_line(line))
    frames.extend(t.finalize())
    kinds = [f[0] for f in frames]
    assert "tool_use_start" in kinds
    assert "tool_use_end" in kinds
    assert "text_delta" in kinds
    assert "usage" in kinds
    assert t.result.final_text == "Repo scanned."
    assert t.result.usage["input_tokens"] == 100
    assert t.result.usage["cache_read_tokens"] == 50


def test_codex_build_argv():
    argv = cli_exec.get_exec_spec("codex").build_argv(prompt="p", workdir="/workspace")
    assert argv[:3] == ["codex", "exec", "--json"]
    assert "p" in argv


def test_cursor_translator_maps_stream():
    spec = cli_exec.get_exec_spec("cursor")
    t = spec.new_translator()
    frames: list[tuple[str, dict]] = []
    lines = [
        '{"type":"system","subtype":"init"}',
        '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I found the bug."}]}}',  # noqa: E501
        '{"type":"tool_call","subtype":"started","call_id":"c1","tool_call":{"readToolCall":{"args":{"path":"m.rs"}}}}',
        '{"type":"tool_call","subtype":"completed","call_id":"c1","tool_call":{"readToolCall":{"result":{"success":{"content":"fn main(){}"}}}}}',  # noqa: E501
        '{"type":"result","result":"All fixed.","session_id":"s1"}',
    ]
    for line in lines:
        frames.extend(t.on_line(line))
    frames.extend(t.finalize())
    kinds = [f[0] for f in frames]
    assert "text_delta" in kinds
    assert "tool_use_start" in kinds
    assert "tool_use_end" in kinds
    # tool name derived from readToolCall → read
    start = next(f for f in frames if f[0] == "tool_use_start")
    assert start[1]["tool_name"] == "read"
    assert t.result.final_text == "All fixed."


def test_cursor_build_argv():
    argv = cli_exec.get_exec_spec("cursor").build_argv(prompt="p", workdir="/workspace")
    assert argv[0] == "cursor-agent"
    assert "stream-json" in argv and "--force" in argv


# ─── SandboxedCliWorker ──────────────────────────────────────────────────


class _FakeSandbox(Sandbox):
    kind = "fake"
    is_remote = True

    def __init__(self) -> None:
        super().__init__()
        self.state = "open"
        self.sandbox_id = "fake-1"
        self.paused = False

    async def exec_shell(self, command, *, timeout_seconds=None, cwd=None, env=None,
                         on_stdout=None, on_stderr=None) -> ExecResult:
        lines = [
            '{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"Hi"}}}',
            '{"type":"result","subtype":"success","result":"All done.","total_cost_usd":0.0}',
        ]
        if on_stdout is not None:
            for line in lines:
                ret = on_stdout(line)
                if asyncio.iscoroutine(ret):
                    await ret
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def pause(self) -> None:
        self.paused = True


async def test_sandboxed_worker_emits_and_pauses(monkeypatch):
    from agent_team.features.board.runtime.workers import sandboxed_cli as sc
    from agent_team.features.board.runtime.workers.base import TurnContext

    fake = _FakeSandbox()

    async def _prepare(**kwargs):
        return fake

    paused = {"v": False}

    async def _pause(task_id):
        paused["v"] = True

    monkeypatch.setattr(sc, "prepare_task_sandbox", _prepare)
    monkeypatch.setattr(sc, "pause_task_sandbox", _pause)
    monkeypatch.setattr(
        sc, "resolve_profile", lambda *a, **k: RuntimeProfile(provider="opensandbox")
    )

    worker = sc.SandboxedCliWorker(engine="claude")
    ctx = TurnContext(
        run_id="r1", agent_alias="cli:claude", prompt="go",
        workspace_path="/tmp/ws", thread_id="th1", task_id="TASK-9",
    )
    emitted: list[tuple[str, dict]] = []

    async def emit(t, d):
        emitted.append((t, d))

    result = await worker.run_turn(ctx, emit, asyncio.Event())
    kinds = [e[0] for e in emitted]
    assert "text_delta" in kinds
    assert result.final_text == "All done."
    assert paused["v"] is True


async def test_sandboxed_worker_strict_no_host_fallback(monkeypatch):
    from agent_team.features.board.runtime.workers import sandboxed_cli as sc
    from agent_team.features.board.runtime.workers.base import TurnContext

    async def _prepare(**kwargs):
        raise SandboxError("no server")

    host_spawned = {"v": False}

    async def _fake_fallback(self, ctx, emit, cancel):
        host_spawned["v"] = True
        from agent_team.features.board.runtime.workers.base import TurnResult

        return TurnResult(final_text="host", cancelled=False, usage=ctx.usage)

    monkeypatch.setattr(sc, "prepare_task_sandbox", _prepare)
    monkeypatch.setattr(
        sc, "resolve_profile",
        lambda *a, **k: RuntimeProfile(provider="opensandbox", strict_isolation=True),
    )
    monkeypatch.setattr(sc.SandboxedCliWorker, "_fallback_host", _fake_fallback)

    worker = sc.SandboxedCliWorker(engine="claude")
    ctx = TurnContext(
        run_id="r2", agent_alias="cli:claude", prompt="go",
        workspace_path="/tmp/ws", thread_id="th2", task_id="TASK-10",
    )
    emitted: list[tuple[str, dict]] = []

    async def emit(t, d):
        emitted.append((t, d))

    result = await worker.run_turn(ctx, emit, asyncio.Event())
    assert host_spawned["v"] is False  # strict → never runs on host
    assert any(e[0] == "error" for e in emitted)
    assert "could not be prepared" in result.final_text


# ─── ACP sidecar protocol + worker (Phase 2) ─────────────────────────────


def test_sidecar_protocol_roundtrip():
    from agent_team.features.board.runtime.sandbox import sidecar_protocol as p

    req = p.decode(p.encode(p.turn_request(
        engine="claude", prompt="hi", cwd="/workspace", thread_id="t1",
        secrets=["sk-1"],
    )))
    assert req["type"] == p.MSG_TURN
    assert req["engine"] == "claude"
    assert req["secrets"] == ["sk-1"]

    fr = p.decode(p.encode(p.frame("text_delta", {"text": "yo"})))
    assert fr["type"] == p.MSG_FRAME and fr["event"] == "text_delta"

    res = p.decode(p.encode(p.result(
        final_text="done", cancelled=False, ok=True, usage={"total_tokens": 5},
    )))
    assert res["type"] == p.MSG_RESULT and res["usage"]["total_tokens"] == 5


async def test_sidecar_worker_relays_frames(monkeypatch):
    import websockets
    from agent_team.features.board.runtime.sandbox import sidecar_protocol as p
    from agent_team.features.board.runtime.workers import sidecar_acp as sa
    from agent_team.features.board.runtime.workers.base import TurnContext

    async def handler(ws):
        await ws.send(p.encode(p.hello(engines=["claude"])))
        req = p.decode(await ws.recv())
        assert req["engine"] == "claude" and req["prompt"] == "go"
        await ws.send(p.encode(p.frame("text_delta", {"text": "Hello "})))
        await ws.send(p.encode(p.frame("text_delta", {"text": "world"})))
        await ws.send(p.encode(p.result(
            final_text="world", cancelled=False, ok=True,
            usage={"total_tokens": 7}, cli_usage_text="7 tok",
        )))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}/acp"

        fake = _FakeSandbox()

        async def _prepare(**kwargs):
            return fake

        async def _channel(sandbox, profile):
            return url, {}

        paused = {"v": False}

        async def _pause(task_id):
            paused["v"] = True

        monkeypatch.setattr(sa, "prepare_task_sandbox", _prepare)
        monkeypatch.setattr(sa, "open_sidecar_channel", _channel)
        monkeypatch.setattr(sa, "pause_task_sandbox", _pause)
        monkeypatch.setattr(
            sa, "resolve_profile",
            lambda *a, **k: RuntimeProfile(
                provider="opensandbox", runtime_strategy="acp_sidecar"
            ),
        )

        worker = sa.SidecarAcpWorker(engine="claude")
        ctx = TurnContext(
            run_id="r3", agent_alias="cli:claude", prompt="go",
            workspace_path="/tmp/ws", thread_id="th3", task_id="TASK-11",
        )
        emitted: list[tuple[str, dict]] = []

        async def emit(t, d):
            emitted.append((t, d))

        result = await worker.run_turn(ctx, emit, asyncio.Event())

    kinds = [e[0] for e in emitted]
    assert kinds.count("text_delta") == 2
    assert result.final_text == "world"
    assert result.usage["total_tokens"] == 7
    assert paused["v"] is True


async def test_sidecar_worker_strict_fail_when_unavailable(monkeypatch):
    from agent_team.features.board.runtime.workers import sidecar_acp as sa
    from agent_team.features.board.runtime.workers.base import TurnContext

    async def _prepare(**kwargs):
        raise SandboxError("no server")

    monkeypatch.setattr(sa, "prepare_task_sandbox", _prepare)
    monkeypatch.setattr(
        sa, "resolve_profile",
        lambda *a, **k: RuntimeProfile(
            provider="opensandbox", runtime_strategy="acp_sidecar",
            strict_isolation=True,
        ),
    )

    worker = sa.SidecarAcpWorker(engine="claude")
    ctx = TurnContext(
        run_id="r4", agent_alias="cli:claude", prompt="go",
        workspace_path="/tmp/ws", thread_id="th4", task_id="TASK-12",
    )
    emitted: list[tuple[str, dict]] = []

    async def emit(t, d):
        emitted.append((t, d))

    result = await worker.run_turn(ctx, emit, asyncio.Event())
    assert any(e[0] == "error" for e in emitted)
    assert "could not be prepared" in result.final_text


# ─── credential injection (config_dir mounts from the AI Code pool) ────────


def _resolved(**kwargs):
    """Build a :class:`ResolvedAccount` (the ORM-free input backends consume)."""
    from agent_team.features.board.runtime.credentials.spec import ResolvedAccount

    return ResolvedAccount(**kwargs)


def _header_req(**kwargs):
    """A header_token requirement for exercising the env/vault backends directly.

    No provider defaults to these backends anymore (both coding agents mount a
    login folder), but the backends are kept for future API-key providers, so we
    test them at the backend seam with a hand-built requirement.
    """
    from agent_team.features.board.runtime.credentials.spec import (
        CredentialRequirement,
    )

    base = dict(
        name="anthropic-oauth",
        kind="header_token",
        hosts=["api.anthropic.com"],
        secret_sandbox_env="CLAUDE_CODE_OAUTH_TOKEN",
        auth_type="bearer",
    )
    base.update(kwargs)
    return CredentialRequirement(**base)


def test_mount_backend_mounts_claude_config():
    from agent_team.features.board.runtime.credentials.injector import build_plan

    acc = _resolved(
        name="claude-acc1", provider="claude", backend="mount",
        material={"host_path": "/var/lib/at/claude/acc1"},
    )
    plan = build_plan(acc)
    assert len(plan.mounts) == 1
    mount = plan.mounts[0]
    assert mount.kind == "host"
    assert mount.host_path == "/var/lib/at/claude/acc1"
    assert mount.mount_path == "/root/.claude"
    assert mount.read_only is False
    assert plan.env["CLAUDE_CONFIG_DIR"] == "/root/.claude"
    assert plan.env["IS_SANDBOX"] == "1"
    assert {"action": "allow", "target": "api.anthropic.com"} in plan.network_rules


def test_mount_backend_mounts_codex_home():
    from agent_team.features.board.runtime.credentials.injector import build_plan

    acc = _resolved(
        name="codex-acc1", provider="codex", backend="mount",
        material={"host_path": "/var/lib/at/codex/acc1"},
    )
    plan = build_plan(acc)
    assert len(plan.mounts) == 1
    mount = plan.mounts[0]
    assert mount.kind == "host"
    assert mount.host_path == "/var/lib/at/codex/acc1"
    assert mount.mount_path == "/root/.codex"
    assert mount.read_only is False
    assert plan.env["CODEX_HOME"] == "/root/.codex"


def test_mount_backend_supports_pvc_claim():
    from agent_team.features.board.runtime.credentials.injector import build_plan

    acc = _resolved(
        name="codex-acc2", provider="codex", backend="mount",
        material={"pvc_claim": "at-codex-acc2"},
    )
    plan = build_plan(acc)
    assert plan.mounts[0].kind == "pvc"
    assert plan.mounts[0].pvc_claim == "at-codex-acc2"


def test_mount_backend_missing_material_raises():
    from agent_team.features.board.runtime.credentials.backends.base import (
        CredentialError,
    )
    from agent_team.features.board.runtime.credentials.injector import build_plan

    acc = _resolved(name="codex-acc3", provider="codex", backend="mount")
    with pytest.raises(CredentialError):
        build_plan(acc)


def test_injector_uses_provider_default_backend():
    """No explicit backend → both claude and codex default to mount."""
    from agent_team.features.board.runtime.credentials.injector import build_plan

    claude = _resolved(
        name="c", provider="claude", backend="",
        material={"host_path": "/claude"},
    )
    assert build_plan(claude).env["CLAUDE_CONFIG_DIR"] == "/root/.claude"

    codex = _resolved(
        name="d", provider="codex", backend="",
        material={"host_path": "/codex"},
    )
    assert build_plan(codex).env["CODEX_HOME"] == "/root/.codex"


def test_injector_unknown_provider_raises():
    from agent_team.features.board.runtime.credentials.backends.base import (
        CredentialError,
    )
    from agent_team.features.board.runtime.credentials.injector import build_plan

    acc = _resolved(name="x", provider="gemini", backend="mount")
    with pytest.raises(CredentialError):
        build_plan(acc)


def test_env_backend_injects_token(monkeypatch):
    """The env backend forwards a host secret env into the sandbox (backend seam)."""
    from agent_team.features.board.runtime.credentials.backends.env_backend import (
        EnvBackend,
    )

    monkeypatch.setenv("MY_CLAUDE_TOKEN", "sk-ant-oat01-secret")
    acc = _resolved(
        name="k", provider="claude", backend="env",
        material={"secret_env": "MY_CLAUDE_TOKEN"},
    )
    plan = EnvBackend().plan(acc, _header_req())
    assert plan.env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-secret"
    assert not plan.mounts
    assert {"action": "allow", "target": "api.anthropic.com"} in plan.network_rules


def test_env_backend_missing_host_env_raises(monkeypatch):
    from agent_team.features.board.runtime.credentials.backends.base import (
        CredentialError,
    )
    from agent_team.features.board.runtime.credentials.backends.env_backend import (
        EnvBackend,
    )

    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    acc = _resolved(
        name="k", provider="claude", backend="env",
        material={"secret_env": "MISSING_TOKEN"},
    )
    with pytest.raises(CredentialError):
        EnvBackend().plan(acc, _header_req())


def test_vault_backend_builds_credential_and_binding(monkeypatch):
    from agent_team.features.board.runtime.credentials.backends.vault_backend import (
        VaultBackend,
    )

    monkeypatch.setenv("HOST_CLAUDE_TOKEN", "sk-ant-oat01-real")
    acc = _resolved(
        name="k", provider="claude", backend="vault",
        material={"secret_env": "HOST_CLAUDE_TOKEN"},
    )
    plan = VaultBackend().plan(acc, _header_req())

    # Real token never lands in the sandbox — only a placeholder.
    assert plan.env["CLAUDE_CODE_OAUTH_TOKEN"] != "sk-ant-oat01-real"
    assert "sk-ant-oat01-real" not in plan.env.values()
    assert plan.needs_credential_proxy is True

    # Real secret is carried in the vault credential (goes to the proxy only).
    assert plan.vault_credentials == [
        {"name": "anthropic-oauth",
         "source": {"value": "sk-ant-oat01-real", "type": "inline"}}
    ]
    binding = plan.vault_bindings[0]
    assert binding["match"]["hosts"] == ["api.anthropic.com"]
    assert binding["auth"] == {"type": "bearer", "credential": "anthropic-oauth"}
    assert {"action": "allow", "target": "api.anthropic.com"} in plan.network_rules


def test_vault_backend_rejects_config_dir():
    from agent_team.features.board.runtime.credentials.backends.base import (
        CredentialError,
    )
    from agent_team.features.board.runtime.credentials.backends.vault_backend import (
        VaultBackend,
    )
    from agent_team.features.board.runtime.credentials.spec import (
        CredentialRequirement,
    )

    req = CredentialRequirement(
        name="codex-home", kind="config_dir",
        target_dir_env="CODEX_HOME", mount_path="/root/.codex",
    )
    acc = _resolved(name="k", provider="codex", backend="vault",
                    material={"secret_env": "X"})
    with pytest.raises(CredentialError):
        VaultBackend().plan(acc, req)


# ─── network policy resolution (Đợt 2) ─────────────────────────────────────


def _profile(**kwargs):
    return RuntimeProfile(provider="opensandbox", **kwargs)


def test_network_policy_none_when_allow_all_no_rules():
    from agent_team.features.board.runtime.sandbox.service import (
        _resolve_network_policy,
    )

    assert _resolve_network_policy(_profile(), None) is None


def test_network_policy_strict_denies_by_default_with_credential():
    from agent_team.features.board.runtime.credentials.injector import build_plan
    from agent_team.features.board.runtime.sandbox.service import (
        _resolve_network_policy,
    )

    acc = _resolved(
        name="c", provider="codex", backend="mount",
        material={"host_path": "/p"},
    )
    plan = build_plan(acc)
    policy = _resolve_network_policy(_profile(strict_isolation=True), plan)
    assert policy is not None
    assert policy["default_action"] == "deny"
    targets = {r["target"] for r in policy["egress"]}
    assert "api.openai.com" in targets and "chatgpt.com" in targets


def test_network_policy_merges_board_base_and_dedupes():
    from agent_team.features.board.runtime.credentials.injector import build_plan
    from agent_team.features.board.runtime.sandbox.service import (
        _resolve_network_policy,
    )

    monkeypatch_env = _profile(
        network_policy={
            "default_action": "deny",
            "egress": [{"action": "allow", "target": "pypi.org"}],
        },
    )
    acc = _resolved(
        name="c", provider="codex", backend="mount",
        material={"host_path": "/p"},
    )
    policy = _resolve_network_policy(monkeypatch_env, build_plan(acc))
    targets = [r["target"] for r in policy["egress"]]
    assert "pypi.org" in targets
    assert targets.count("api.openai.com") == 1  # deduped


# ─── OpenSandboxRuntime: network policy + credential proxy + vault (Đợt 2/3) ─


async def test_opensandbox_open_passes_network_policy_and_proxy(monkeypatch):
    monkeypatch.setattr(osb, "_import_sdk", _fake_sdk)
    rt = osb.OpenSandboxRuntime(
        image="img:test",
        renew_interval_seconds=3600,
        network_policy={
            "default_action": "deny",
            "egress": [{"action": "allow", "target": "api.anthropic.com"}],
        },
        credential_proxy=True,
    )
    await rt.open()
    kw = _FakeSdkSandbox.last_create_kwargs
    assert kw["network_policy"].kwargs["default_action"] == "deny"
    assert kw["credential_proxy"].kwargs["enabled"] is True
    await rt.close()


async def test_opensandbox_write_vault(monkeypatch):
    monkeypatch.setattr(osb, "_import_sdk", _fake_sdk)
    rt = osb.OpenSandboxRuntime(image="img:test", renew_interval_seconds=3600)
    await rt.open()
    await rt.write_vault(
        [{"name": "anthropic-oauth",
          "source": {"value": "secret", "type": "inline"}}],
        [{"name": "anthropic-oauth",
          "match": {"hosts": ["api.anthropic.com"]},
          "auth": {"type": "bearer", "credential": "anthropic-oauth"}}],
    )
    vault = rt._sdk_sandbox.credential_vault
    assert len(vault.created) == 1
    assert vault.created[0]["credentials"][0]["name"] == "anthropic-oauth"
    await rt.close()


def test_build_injection_for_board_empty_returns_none():
    from agent_team.features.board.runtime.credentials.service import (
        build_injection_for_board,
    )

    assert build_injection_for_board(None) is None
    assert build_injection_for_board("") is None


class _FakeBoard:
    def __init__(self, aliases, mcp):
        self._aliases = aliases
        self._mcp = mcp

    def cli_target_ids(self):
        return self._aliases

    def agent_mcp(self):
        return self._mcp


def test_providers_for_board_maps_cli_aliases_deduped():
    from agent_team.features.board.runtime.credentials.service import (
        _providers_for_board,
    )

    board = _FakeBoard(
        ["cli:claude", "cli:codex", "cli:claude", "agent-99"], {}
    )
    assert _providers_for_board(board) == ["claude", "codex"]


def test_mcp_allow_rules_extracts_remote_hosts_only():
    from agent_team.features.board.runtime.credentials.service import (
        _mcp_allow_rules,
    )

    board = _FakeBoard(
        [],
        {
            "cli:claude": {
                "mcpServers": {
                    "docs": {"url": "https://mcp.example.com/sse"},
                    "local": {"command": "npx", "args": ["-y", "thing"]},
                }
            },
            "cli:codex": {
                "mcpServers": {"docs2": {"url": "https://mcp.example.com/x"}}
            },
        },
    )
    rules = _mcp_allow_rules(board)
    # stdio (command) skipped; remote host added once (deduped across agents).
    assert rules == [{"action": "allow", "target": "mcp.example.com"}]


# ─── persisted sandbox id: reattach after an app restart ───────────────────


class _ReattachableSandbox(Sandbox):
    """Fake with the OpenSandbox-style reattach API."""

    kind = "fake"
    is_remote = True

    def __init__(self, *, fail_resume: bool = False) -> None:
        super().__init__()
        self.state = "closed"
        self.sandbox_id = None
        self.fail_resume = fail_resume
        self.resumed_with: str | None = None
        self.closed = False

    async def resume_existing(self, sandbox_id: str) -> None:
        if self.fail_resume:
            self.state = "broken"
            raise SandboxError("sandbox gone")
        self.sandbox_id = sandbox_id
        self.resumed_with = sandbox_id
        self.state = "open"

    async def close(self) -> None:
        self.closed = True
        self.state = "closed"

    async def exec_shell(self, command, *, timeout_seconds=None, cwd=None, env=None,
                         on_stdout=None, on_stderr=None) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)


async def test_manager_adopt_tracks_and_closes():
    mgr = SandboxManager(profile=RuntimeProfile(provider="opensandbox"))
    sb = _ReattachableSandbox()
    await sb.resume_existing("sb-live")
    await mgr.adopt("T-1", sb)
    assert mgr.get("T-1") is sb
    with pytest.raises(ValueError):
        await mgr.adopt("T-1", _ReattachableSandbox())
    assert await mgr.close("T-1") is True
    assert sb.closed and mgr.get("T-1") is None


async def test_try_reattach_success_adopts_persisted_sandbox(monkeypatch):
    from agent_team.features.board.runtime.sandbox import factory as factory_mod
    from agent_team.features.board.runtime.sandbox import service as svc

    sb = _ReattachableSandbox()
    monkeypatch.setattr(svc, "_load_task_sandbox_id", lambda _t: "sb-persisted")
    monkeypatch.setattr(factory_mod, "build_sandbox", lambda *a, **k: sb)

    mgr = SandboxManager(profile=RuntimeProfile(provider="opensandbox"))
    got = await svc._try_reattach_sandbox(
        mgr, "T-2", RuntimeProfile(provider="opensandbox")
    )
    assert got is sb
    assert sb.resumed_with == "sb-persisted"
    assert mgr.get("T-2") is sb


async def test_try_reattach_stale_id_clears_and_falls_back(monkeypatch):
    from agent_team.features.board.runtime.sandbox import factory as factory_mod
    from agent_team.features.board.runtime.sandbox import service as svc

    sb = _ReattachableSandbox(fail_resume=True)
    cleared: list[tuple[str, str | None]] = []
    monkeypatch.setattr(svc, "_load_task_sandbox_id", lambda _t: "sb-stale")
    monkeypatch.setattr(
        svc, "_store_task_sandbox_id", lambda t, v: cleared.append((t, v))
    )
    monkeypatch.setattr(factory_mod, "build_sandbox", lambda *a, **k: sb)

    mgr = SandboxManager(profile=RuntimeProfile(provider="opensandbox"))
    got = await svc._try_reattach_sandbox(
        mgr, "T-3", RuntimeProfile(provider="opensandbox")
    )
    assert got is None
    assert ("T-3", None) in cleared  # stale id wiped so the next open overwrites
    assert sb.closed
    assert mgr.get("T-3") is None


async def test_try_reattach_skips_when_nothing_persisted(monkeypatch):
    from agent_team.features.board.runtime.sandbox import service as svc

    monkeypatch.setattr(svc, "_load_task_sandbox_id", lambda _t: "")
    mgr = SandboxManager(profile=RuntimeProfile(provider="opensandbox"))
    got = await svc._try_reattach_sandbox(
        mgr, "T-4", RuntimeProfile(provider="opensandbox")
    )
    assert got is None
