import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  useCommConnectionMutations,
  useCommProviders,
  useCommUserLinkMutations,
  useCommUserLinks,
} from "@/api/hooks";
import type {
  CommConnectionCreateBody,
  CommConnectionDTO,
  CommConnectionUpdateBody,
  CommProviderField,
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
import { Spinner } from "@/components/ui/spinner";

/** Connection-column keys a provider field can map to. */
const VALUE_KEYS = ["server_url", "deep_link_base", "default_team_id"] as const;

/**
 * Create or edit a messaging connection. The form is provider-driven: the set of
 * fields comes from the provider descriptor (`/comm/providers`), so adding a new
 * provider needs no UI change. The bot token is write-only.
 */
export function ConnectionDialog({
  connection,
  open,
  onClose,
}: {
  connection: CommConnectionDTO | null;
  open: boolean;
  onClose: () => void;
}) {
  const { create, patch } = useCommConnectionMutations();
  const providers = useCommProviders();
  const editing = !!connection;

  const [provider, setProvider] = useState("mattermost");
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);

  const descriptors = providers.data ?? [];
  const descriptor = useMemo(
    () => descriptors.find((d) => d.id === provider) ?? descriptors[0],
    [descriptors, provider],
  );

  useEffect(() => {
    if (!open) return;
    const initialProvider = connection?.provider ?? descriptors[0]?.id ?? "mattermost";
    setProvider(initialProvider);
    setName(connection?.name ?? "");
    setValues({
      server_url: connection?.server_url ?? "",
      deep_link_base: connection?.deep_link_base ?? "",
      default_team_id: connection?.default_team_id ?? "",
    });
    setToken("");
    setClearToken(false);
    // descriptors load async; re-run when they arrive so provider defaults settle.
  }, [open, connection, descriptors.length]);

  const busy = create.isPending || patch.isPending;
  const tokenConfigured = editing && !!connection?.has_token && !clearToken && !token;

  const save = async () => {
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    const fields = descriptor?.fields ?? [];
    for (const f of fields) {
      if (!f.required) continue;
      if (f.type === "secret") {
        if (!editing && !token.trim()) {
          toast.error(`${f.label} is required`);
          return;
        }
      } else if (!(values[f.key] ?? "").trim()) {
        toast.error(`${f.label} is required`);
        return;
      }
    }

    let botToken: string | null | undefined;
    if (clearToken) botToken = "";
    else if (token) botToken = token;
    else botToken = undefined;

    const fieldKeys = new Set(fields.map((f) => f.key));
    const body: CommConnectionUpdateBody = { name: name.trim() };
    for (const key of VALUE_KEYS) {
      if (fieldKeys.has(key)) body[key] = (values[key] ?? "").trim() || null;
    }
    if (botToken !== undefined) body.bot_token = botToken;

    try {
      if (editing) {
        await patch.mutateAsync({ connectionId: connection.id, body });
        toast.success("Connection updated");
      } else {
        const createBody: CommConnectionCreateBody = { provider, ...body, name: name.trim() };
        await create.mutateAsync(createBody);
        toast.success("Connection added");
        onClose();
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save connection");
    }
  };

  const providerOptions = descriptors.map((d) => ({ value: d.id, label: d.label }));

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit connection" : "Add connection"}</DialogTitle>
        </DialogHeader>

        {providers.isLoading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Spinner className="h-4 w-4" /> Loading…
          </div>
        ) : (
          <div className="grid max-h-[70vh] gap-3 overflow-y-auto pr-1 pt-1">
            <Field label="Provider">
              {editing ? (
                <Input value={descriptor?.label ?? provider} disabled />
              ) : (
                <SelectMenu
                  value={provider}
                  onChange={setProvider}
                  options={providerOptions}
                />
              )}
            </Field>
            <Field label="Name">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Team workspace"
              />
            </Field>

            {(descriptor?.fields ?? []).map((f) =>
              f.type === "secret" ? (
                <Field key={f.key} label={f.label}>
                  <Input
                    type="password"
                    value={token}
                    onChange={(e) => {
                      setToken(e.target.value);
                      setClearToken(false);
                    }}
                    placeholder={
                      tokenConfigured ? "•••••••• (stored — leave blank to keep)" : f.placeholder
                    }
                    autoComplete="new-password"
                  />
                  {editing && connection?.has_token && !clearToken && (
                    <button
                      type="button"
                      onClick={() => {
                        setToken("");
                        setClearToken(true);
                      }}
                      className="justify-self-start pt-1 text-[12px] text-destructive hover:underline"
                    >
                      Clear stored token
                    </button>
                  )}
                  {f.help && (
                    <p className="pt-1 text-[12px] text-muted-foreground">{f.help}</p>
                  )}
                </Field>
              ) : (
                <PlainField
                  key={f.key}
                  field={f}
                  value={values[f.key] ?? ""}
                  onChange={(v) => setValues((cur) => ({ ...cur, [f.key]: v }))}
                />
              ),
            )}

            {editing && <UserMappingSection connectionId={connection.id} />}
          </div>
        )}

        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            {editing ? "Close" : "Cancel"}
          </Button>
          <Button onClick={save} disabled={busy || !descriptor}>
            {editing ? "Save" : "Add connection"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PlainField({
  field,
  value,
  onChange,
}: {
  field: CommProviderField;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <Field label={field.label}>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.placeholder}
      />
      {field.help && <p className="pt-1 text-[12px] text-muted-foreground">{field.help}</p>}
    </Field>
  );
}

function UserMappingSection({ connectionId }: { connectionId: string }) {
  const links = useCommUserLinks(connectionId);
  const { upsert, autoMatch } = useCommUserLinkMutations(connectionId);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const rows = links.data ?? [];

  const runAutoMatch = async () => {
    try {
      const res = await autoMatch.mutateAsync();
      toast.success(`Matched ${res.matched} user${res.matched === 1 ? "" : "s"} by email`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Auto-match failed");
    }
  };

  const saveOne = async (userId: string) => {
    const value = (drafts[userId] ?? "").trim().replace(/^@/, "");
    try {
      await upsert.mutateAsync({ user_id: userId, mm_username: value || null });
      setDrafts((d) => {
        const next = { ...d };
        delete next[userId];
        return next;
      });
      toast.success("Mapping saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save mapping");
    }
  };

  return (
    <div className="mt-1 grid gap-2 rounded-md border border-border p-3">
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-medium text-foreground">User mapping</span>
        <Button
          variant="secondary"
          size="sm"
          onClick={runAutoMatch}
          disabled={autoMatch.isPending}
        >
          {autoMatch.isPending ? <Spinner className="h-3.5 w-3.5" /> : null}
          Auto-match by email
        </Button>
      </div>
      <p className="text-[12px] text-muted-foreground">
        Maps board members to provider usernames so notifications can @mention them. Members
        appear here once a board is linked to this connection.
      </p>
      {links.isLoading ? (
        <div className="flex items-center gap-2 py-2 text-[12.5px] text-muted-foreground">
          <Spinner className="h-3.5 w-3.5" /> Loading…
        </div>
      ) : rows.length === 0 ? (
        <p className="py-1 text-[12.5px] text-muted-foreground">
          No board members yet. Link this connection to a board first.
        </p>
      ) : (
        <div className="grid gap-1.5">
          {rows.map((row) => {
            const draft = drafts[row.user_id];
            const dirty =
              draft !== undefined &&
              draft.trim().replace(/^@/, "") !== (row.mm_username ?? "");
            return (
              <div key={row.user_id} className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-medium text-foreground">
                    {row.display_name || row.email || row.user_id}
                  </div>
                  <div className="truncate text-[11.5px] text-muted-foreground">
                    {row.email}
                  </div>
                </div>
                <Input
                  value={draft ?? row.mm_username ?? ""}
                  onChange={(e) =>
                    setDrafts((d) => ({ ...d, [row.user_id]: e.target.value }))
                  }
                  placeholder="username"
                  className="h-8 w-40"
                />
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!dirty || upsert.isPending}
                  onClick={() => saveOne(row.user_id)}
                >
                  Save
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-1.5">
      <span className="text-[12.5px] font-medium text-foreground">{label}</span>
      {children}
    </label>
  );
}
