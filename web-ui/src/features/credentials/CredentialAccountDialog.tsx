import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useCredentialAccountMutations,
  useCredentialProviders,
} from "@/api/hooks";
import type {
  CredentialAccountCreateBody,
  CredentialAccountDTO,
  CredentialProviderInfo,
} from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SelectMenu } from "@/components/ui/select-menu";

const BACKEND_LABELS: Record<string, string> = {
  env: "Env var (secret injected into sandbox)",
  vault: "Credential Vault (zero-secret, egress proxy)",
  mount: "Mount folder (host path / volume)",
};

const MATERIAL_META: Record<
  string,
  { label: string; placeholder: string; hint: string }
> = {
  secret_env: {
    label: "Host env var holding the secret",
    placeholder: "CLAUDE_CODE_OAUTH_TOKEN",
    hint: "Name of an env var on the host/server process. The value never leaves the host — only its name is stored.",
  },
  host_path: {
    label: "Host directory to mount",
    placeholder: "/var/lib/agent-team/credentials/codex",
    hint: "Absolute path on the host containing the config (e.g. Codex's auth.json). Must be in the server's allowed_host_paths.",
  },
  pvc_claim: {
    label: "Volume / PVC claim (optional)",
    placeholder: "codex-home",
    hint: "Use instead of a host path when running on Kubernetes. Leave blank if using a host path.",
  },
};

/**
 * Create or edit a credential account. The form is driven by provider metadata:
 * picking a provider narrows the valid backends and the reference fields to
 * collect. No secret is entered here — only *references* (a host env-var name or
 * a host path). The real material lives on the host and is resolved at runtime.
 */
export function CredentialAccountDialog({
  account,
  open,
  onClose,
}: {
  account: CredentialAccountDTO | null;
  open: boolean;
  onClose: () => void;
}) {
  const providersQuery = useCredentialProviders();
  const { create, patch } = useCredentialAccountMutations();
  const editing = !!account;
  const providers = useMemo(
    () => providersQuery.data ?? [],
    [providersQuery.data],
  );

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [provider, setProvider] = useState("");
  const [backend, setBackend] = useState("");
  const [material, setMaterial] = useState<Record<string, string>>({});
  const [enabled, setEnabled] = useState(true);
  const [weight, setWeight] = useState("1");
  const [maxConcurrency, setMaxConcurrency] = useState("1");

  useEffect(() => {
    if (!open) return;
    const first = providers[0]?.provider ?? "";
    setName(account?.name ?? "");
    setDescription(account?.description ?? "");
    setProvider(account?.provider ?? first);
    setBackend(account?.backend ?? "");
    setMaterial(account?.material_ref ?? {});
    setEnabled(account?.enabled ?? true);
    setWeight(String(account?.weight ?? 1));
    setMaxConcurrency(String(account?.max_concurrency ?? 1));
  }, [open, account, providers]);

  const info: CredentialProviderInfo | undefined = providers.find(
    (p) => p.provider === provider,
  );
  const backendOptions = [
    { value: "", label: "Provider default" },
    ...(info?.backends ?? []).map((b) => ({
      value: b,
      label: BACKEND_LABELS[b] ?? b,
    })),
  ];
  const effectiveBackend = backend || info?.backends[0] || "";
  const materialKeys = info?.material_keys ?? [];

  const busy = create.isPending || patch.isPending;

  const save = async () => {
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (!provider) {
      toast.error("Pick a provider");
      return;
    }
    const cleanedMaterial: Record<string, string> = {};
    for (const key of materialKeys) {
      const v = (material[key] ?? "").trim();
      if (v) cleanedMaterial[key] = v;
    }
    const body: CredentialAccountCreateBody = {
      name: name.trim(),
      description: description.trim(),
      provider,
      backend,
      material_ref: cleanedMaterial,
      enabled,
      weight: Number(weight) || 1,
      max_concurrency: Number(maxConcurrency) || 1,
    };
    try {
      if (editing) {
        await patch.mutateAsync({ accountId: account.id, body });
        toast.success("Credential account updated");
      } else {
        await create.mutateAsync(body);
        toast.success("Credential account added");
      }
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {editing ? "Edit credential account" : "Add credential account"}
          </DialogTitle>
        </DialogHeader>

        <div className="grid max-h-[70vh] gap-3 overflow-y-auto pr-1 pt-1">
          <Field label="Name">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Claude (primary)"
            />
          </Field>
          <Field label="Description (optional)">
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Team subscription account"
            />
          </Field>

          <Field label="Provider">
            <SelectMenu
              value={provider}
              onChange={setProvider}
              options={providers.map((p) => ({
                value: p.provider,
                label: p.label,
              }))}
            />
          </Field>

          <Field label="Injection backend">
            <SelectMenu
              value={backend}
              onChange={setBackend}
              options={backendOptions}
            />
            <p className="pt-1 text-[12px] text-muted-foreground">
              {backend
                ? BACKEND_LABELS[backend]
                : `Provider default: ${BACKEND_LABELS[effectiveBackend] ?? effectiveBackend}`}
            </p>
          </Field>

          {materialKeys.map((key) => {
            const meta = MATERIAL_META[key] ?? {
              label: key,
              placeholder: "",
              hint: "",
            };
            return (
              <Field key={key} label={meta.label}>
                <Input
                  value={material[key] ?? ""}
                  onChange={(e) =>
                    setMaterial((m) => ({ ...m, [key]: e.target.value }))
                  }
                  placeholder={meta.placeholder}
                />
                {meta.hint && (
                  <p className="pt-1 text-[12px] text-muted-foreground">
                    {meta.hint}
                  </p>
                )}
              </Field>
            );
          })}

          <div className="grid grid-cols-2 gap-3">
            <Field label="Weight">
              <Input
                type="number"
                min={1}
                value={weight}
                onChange={(e) => setWeight(e.target.value)}
              />
            </Field>
            <Field label="Max concurrency">
              <Input
                type="number"
                min={1}
                value={maxConcurrency}
                onChange={(e) => setMaxConcurrency(e.target.value)}
              />
            </Field>
          </div>

          <label className="mt-1 flex items-start gap-2.5 rounded-md border border-border p-3">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-[var(--primary)]"
            />
            <span className="grid gap-0.5">
              <span className="text-[13px] font-medium text-foreground">
                Enabled
              </span>
              <span className="text-[12px] text-muted-foreground">
                Disabled accounts are ignored when a board requests this
                identity.
              </span>
            </span>
          </label>
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>
            {editing ? "Save" : "Add account"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-1.5">
      <span className="text-[12.5px] font-medium text-foreground">{label}</span>
      {children}
    </label>
  );
}
