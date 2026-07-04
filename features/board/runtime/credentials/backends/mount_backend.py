"""``mount`` backend — mount a writable login/config dir into the sandbox.

For ``config_dir`` credentials — the login folders both coding agents use:
Claude's ``CLAUDE_CONFIG_DIR`` (``~/.claude``) and Codex's ``$CODEX_HOME`` (with
``auth.json``). The dir must be **writable** because the CLI refreshes and
rewrites its auth state during use. The account's ``material_ref`` points at the
host source (typically an AI Code Factory environment's ``config_dir``):

* ``{"host_path": "/var/lib/agent-team/codex/acc1"}`` — a host bind mount
  (the path must be in the OpenSandbox server's ``allowed_host_paths``), or
* ``{"pvc_claim": "at-codex-acc1"}`` — a Docker named volume / PVC.

Security: the mounted secret is readable by the sandboxed agent; the egress
network policy (deny-by-default + allowlist of ``req.hosts``) is what prevents
exfiltration and is mandatory whenever this backend is used.
"""

from __future__ import annotations

from agent_team.features.board.runtime.credentials.backends.base import (
    CredentialError,
)
from agent_team.features.board.runtime.credentials.spec import (
    CredentialRequirement,
    InjectionPlan,
    ResolvedAccount,
)
from agent_team.features.board.runtime.sandbox.config import VolumeMount


class MountBackend:
    name = "mount"

    def plan(
        self,
        account: ResolvedAccount,
        req: CredentialRequirement,
    ) -> InjectionPlan:
        if req.kind != "config_dir":
            raise CredentialError(
                f"mount backend only supports config_dir credentials, "
                f"got {req.kind!r} for {req.name!r}"
            )
        if not req.mount_path:
            raise CredentialError(
                f"credential {req.name!r} has no mount_path for the config dir"
            )

        material = account.material_ref()
        host_path = material.get("host_path")
        pvc_claim = material.get("pvc_claim")
        if not host_path and not pvc_claim:
            raise CredentialError(
                f"account {account.name!r} needs material_ref.host_path or "
                f"material_ref.pvc_claim (writable {req.name} dir)"
            )

        if pvc_claim:
            mount = VolumeMount(
                name=f"cred-{req.name}",
                mount_path=req.mount_path,
                kind="pvc",
                read_only=False,
                pvc_claim=pvc_claim,
            )
        else:
            mount = VolumeMount(
                name=f"cred-{req.name}",
                mount_path=req.mount_path,
                kind="host",
                read_only=False,
                host_path=host_path,
            )

        plan = InjectionPlan(env=dict(req.static_env), mounts=[mount])
        if req.target_dir_env:
            plan.env[req.target_dir_env] = req.mount_path
        plan.network_rules.extend(
            {"action": "allow", "target": h} for h in req.hosts
        )
        return plan
