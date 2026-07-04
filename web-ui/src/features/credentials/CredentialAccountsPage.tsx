import { useState } from "react";
import { toast } from "sonner";
import { KeyRound, Pencil, Plus, Trash2 } from "@/components/icons";
import {
  useCredentialAccountMutations,
  useCredentialAccounts,
  useMe,
} from "@/api/hooks";
import type { CredentialAccountDTO } from "@/api/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { CredentialAccountDialog } from "./CredentialAccountDialog";

function materialSummary(acc: CredentialAccountDTO): string {
  const m = acc.material_ref ?? {};
  if (m.secret_env) return `env: ${m.secret_env}`;
  if (m.host_path) return `path: ${m.host_path}`;
  if (m.pvc_claim) return `volume: ${m.pvc_claim}`;
  return "no material reference";
}

/** Admin-only registry of provider identities injected into task sandboxes. */
export function CredentialAccountsPage() {
  const me = useMe();
  const accounts = useCredentialAccounts();
  const { remove } = useCredentialAccountMutations();
  const confirm = useConfirm();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<CredentialAccountDTO | null>(null);

  if (me.data && !me.data.is_admin) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Credential management is available to administrators only.
      </div>
    );
  }

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };
  const openEdit = (acc: CredentialAccountDTO) => {
    setEditing(acc);
    setDialogOpen(true);
  };

  const doDelete = async (acc: CredentialAccountDTO) => {
    const ok = await confirm({
      title: `Delete “${acc.name}”?`,
      description:
        "Boards referencing this account will fall back to their default runtime credentials.",
      tone: "danger",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(acc.id);
      toast.success("Credential account deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete");
    }
  };

  const list = accounts.data ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <div className="flex items-center gap-2.5">
          <KeyRound className="h-5 w-5 text-muted-foreground" />
          <div>
            <h1 className="text-[18px] font-semibold text-foreground">
              Credentials
            </h1>
            <p className="text-[12.5px] text-muted-foreground">
              Provider identities injected into isolated task sandboxes.
            </p>
          </div>
        </div>
        <Button onClick={openCreate}>
          <Plus /> Add account
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        {accounts.isLoading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Spinner className="h-4 w-4" /> Loading…
          </div>
        ) : list.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center">
            <KeyRound className="mx-auto h-8 w-8 text-muted-foreground/50" />
            <p className="mt-3 text-sm font-medium text-foreground">
              No credential accounts yet
            </p>
            <p className="mt-1 text-[13px] text-muted-foreground">
              Register a provider identity so boards can run agents with a
              subscription instead of an API key.
            </p>
            <Button className="mt-4" onClick={openCreate}>
              <Plus /> Add account
            </Button>
          </div>
        ) : (
          <div className="grid gap-2">
            {list.map((acc) => (
              <div
                key={acc.id}
                className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3"
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-surface-1">
                  <KeyRound className="h-5 w-5 text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[14px] font-medium text-foreground">
                      {acc.name}
                    </span>
                    <Badge variant="outline">{acc.provider}</Badge>
                    <Badge variant="default">{acc.effective_backend}</Badge>
                    {acc.enabled ? (
                      <Badge variant={acc.ready ? "success" : "destructive"}>
                        {acc.ready ? "Ready" : "Missing material"}
                      </Badge>
                    ) : (
                      <Badge variant="outline">Disabled</Badge>
                    )}
                  </div>
                  {acc.description && (
                    <div className="mt-0.5 truncate text-[12.5px] text-muted-foreground">
                      {acc.description}
                    </div>
                  )}
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12px] text-muted-foreground">
                    <span className="font-mono">{materialSummary(acc)}</span>
                    <span>weight {acc.weight}</span>
                    <span>max {acc.max_concurrency}</span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => openEdit(acc)}
                    title="Edit"
                  >
                    <Pencil />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => doDelete(acc)}
                    title="Delete"
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <CredentialAccountDialog
        account={editing}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  );
}
