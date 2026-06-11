import { useEffect, useState } from "react";
import { toast } from "sonner";
import { useRepoMutations } from "@/api/hooks";
import type { RepoAuthType, RepoDTO, RepoScheduleMode } from "@/api/types";
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
import { Textarea } from "@/components/ui/textarea";

const AUTH_OPTIONS = [
  { value: "none", label: "Public (no credentials)" },
  { value: "token", label: "HTTPS token (PAT)" },
  { value: "ssh", label: "SSH key" },
];

const SCHEDULE_OPTIONS = [
  { value: "off", label: "Off (manual only)" },
  { value: "interval", label: "Every…" },
  { value: "cron", label: "Cron expression" },
];

const INTERVAL_OPTIONS = [
  { value: "900", label: "15 minutes" },
  { value: "1800", label: "30 minutes" },
  { value: "3600", label: "1 hour" },
  { value: "10800", label: "3 hours" },
  { value: "21600", label: "6 hours" },
  { value: "43200", label: "12 hours" },
  { value: "86400", label: "24 hours" },
];

/**
 * Create or edit a code repository. The credential (token / SSH key) is
 * write-only: when editing a repo that already has one stored, the field shows a
 * "configured" hint and stays blank — leaving it blank keeps the stored secret.
 */
export function RepoDialog({
  repo,
  open,
  onClose,
}: {
  repo: RepoDTO | null;
  open: boolean;
  onClose: () => void;
}) {
  const { create, patch } = useRepoMutations();
  const editing = !!repo;

  const [name, setName] = useState("");
  const [gitUrl, setGitUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [authType, setAuthType] = useState<RepoAuthType>("none");
  const [authUsername, setAuthUsername] = useState("");
  const [secret, setSecret] = useState("");
  const [clearSecret, setClearSecret] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<RepoScheduleMode>("off");
  const [interval, setInterval] = useState("3600");
  const [cron, setCron] = useState("");
  const [allowPush, setAllowPush] = useState(false);
  const [committerName, setCommitterName] = useState("");
  const [committerEmail, setCommitterEmail] = useState("");

  useEffect(() => {
    if (!open) return;
    setName(repo?.name ?? "");
    setGitUrl(repo?.git_url ?? "");
    setBranch(repo?.default_branch ?? "");
    setAuthType(repo?.auth_type ?? "none");
    setAuthUsername(repo?.auth_username ?? "");
    setSecret("");
    setClearSecret(false);
    setScheduleMode(repo?.schedule_mode ?? "off");
    setInterval(String(repo?.schedule_interval_seconds ?? 3600));
    setCron(repo?.schedule_cron ?? "");
    setAllowPush(repo?.allow_push ?? false);
    setCommitterName(repo?.committer_name ?? "");
    setCommitterEmail(repo?.committer_email ?? "");
  }, [open, repo]);

  const busy = create.isPending || patch.isPending;

  const save = async () => {
    if (!name.trim() || !gitUrl.trim()) {
      toast.error("Name and Git URL are required");
      return;
    }
    if (scheduleMode === "cron" && !cron.trim()) {
      toast.error("Enter a cron expression");
      return;
    }
    // Secret: include only when the user typed one (set) or explicitly cleared it.
    let authSecret: string | null | undefined;
    if (authType === "none") authSecret = "";
    else if (clearSecret) authSecret = "";
    else if (secret) authSecret = secret;
    else authSecret = undefined;

    const body = {
      name: name.trim(),
      git_url: gitUrl.trim(),
      default_branch: branch.trim() || null,
      auth_type: authType,
      auth_username: authType === "token" ? authUsername.trim() || null : null,
      ...(authSecret === undefined ? {} : { auth_secret: authSecret }),
      schedule_mode: scheduleMode,
      schedule_interval_seconds: Number(interval) || 3600,
      schedule_cron: scheduleMode === "cron" ? cron.trim() : null,
      allow_push: allowPush,
      committer_name: allowPush ? committerName.trim() || null : null,
      committer_email: allowPush ? committerEmail.trim() || null : null,
    };

    try {
      if (editing) {
        await patch.mutateAsync({ repoId: repo.id, body });
        toast.success("Repository updated");
      } else {
        await create.mutateAsync(body);
        toast.success("Repository added");
      }
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save repository");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit repository" : "Add repository"}</DialogTitle>
        </DialogHeader>

        <div className="grid max-h-[70vh] gap-3 overflow-y-auto pr-1 pt-1">
          <Field label="Name">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Backend service"
            />
          </Field>
          <Field label="Git URL">
            <Input
              value={gitUrl}
              onChange={(e) => setGitUrl(e.target.value)}
              placeholder="https://github.com/org/repo.git or git@github.com:org/repo.git"
            />
          </Field>
          <Field label="Default branch (optional)">
            <Input
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              placeholder="leave empty for the remote default"
            />
          </Field>

          <Field label="Authentication">
            <SelectMenu
              value={authType}
              onChange={(v) => setAuthType(v as RepoAuthType)}
              options={AUTH_OPTIONS}
            />
          </Field>

          {authType === "token" && (
            <>
              <Field label="Username (optional)">
                <Input
                  value={authUsername}
                  onChange={(e) => setAuthUsername(e.target.value)}
                  placeholder="x-access-token (default — needed for Bitbucket)"
                />
              </Field>
              <Field label="Personal access token">
                <SecretField
                  editing={editing}
                  hasSecret={!!repo?.has_secret}
                  value={secret}
                  cleared={clearSecret}
                  onChange={(v) => {
                    setSecret(v);
                    setClearSecret(false);
                  }}
                  onClear={() => {
                    setSecret("");
                    setClearSecret(true);
                  }}
                  placeholder="ghp_…"
                  multiline={false}
                />
                <p className="pt-1 text-[12px] text-muted-foreground">
                  Stored server-side and never shown again. Create a token in
                  your{" "}
                  <a
                    className="text-primary hover:underline"
                    href="https://github.com/settings/tokens"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Git provider settings
                  </a>
                  .
                </p>
              </Field>
            </>
          )}

          {authType === "ssh" && (
            <Field label="SSH private key">
              <SecretField
                editing={editing}
                hasSecret={!!repo?.has_secret}
                value={secret}
                cleared={clearSecret}
                onChange={(v) => {
                  setSecret(v);
                  setClearSecret(false);
                }}
                onClear={() => {
                  setSecret("");
                  setClearSecret(true);
                }}
                placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\n…"}
                multiline
              />
            </Field>
          )}

          <Field label="Scheduled pull">
            <SelectMenu
              value={scheduleMode}
              onChange={(v) => setScheduleMode(v as RepoScheduleMode)}
              options={SCHEDULE_OPTIONS}
            />
          </Field>
          {scheduleMode === "interval" && (
            <Field label="Pull every">
              <SelectMenu
                value={interval}
                onChange={setInterval}
                options={INTERVAL_OPTIONS}
              />
            </Field>
          )}
          {scheduleMode === "cron" && (
            <Field label="Cron expression">
              <Input
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                placeholder="0 * * * *"
              />
            </Field>
          )}

          <div className="mt-1 grid gap-2 rounded-md border border-border p-3">
            <label className="flex items-start gap-2.5">
              <input
                type="checkbox"
                checked={allowPush}
                onChange={(e) => setAllowPush(e.target.checked)}
                className="mt-0.5 h-4 w-4 accent-[var(--primary)]"
              />
              <span className="grid gap-0.5">
                <span className="text-[13px] font-medium text-foreground">
                  Allow agents to push
                </span>
                <span className="text-[12px] text-muted-foreground">
                  Lets agents publish commits via the git_push tool, using the
                  credential above. Requires a token/key with write access.
                </span>
              </span>
            </label>
            {allowPush && (
              <div className="grid gap-3 pt-1">
                <Field label="Commit author name (optional)">
                  <Input
                    value={committerName}
                    onChange={(e) => setCommitterName(e.target.value)}
                    placeholder="Agent Team"
                  />
                </Field>
                <Field label="Commit author email (optional)">
                  <Input
                    value={committerEmail}
                    onChange={(e) => setCommitterEmail(e.target.value)}
                    placeholder="agent-team@your-org.com"
                  />
                  <p className="pt-1 text-[12px] text-muted-foreground">
                    Used as the commit identity in task working copies. Hosts map
                    commits to accounts by email.
                  </p>
                </Field>
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>
            {editing ? "Save" : "Add repository"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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

function SecretField({
  editing,
  hasSecret,
  value,
  cleared,
  onChange,
  onClear,
  placeholder,
  multiline,
}: {
  editing: boolean;
  hasSecret: boolean;
  value: string;
  cleared: boolean;
  onChange: (v: string) => void;
  onClear: () => void;
  placeholder: string;
  multiline: boolean;
}) {
  const configured = editing && hasSecret && !cleared && !value;
  return (
    <div className="grid gap-1">
      {multiline ? (
        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={configured ? "•••••••• (stored — leave blank to keep)" : placeholder}
          rows={4}
          className="font-mono text-xs"
        />
      ) : (
        <Input
          type="password"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={configured ? "•••••••• (stored — leave blank to keep)" : placeholder}
          autoComplete="new-password"
        />
      )}
      {editing && hasSecret && !cleared && (
        <button
          type="button"
          onClick={onClear}
          className="justify-self-start text-[12px] text-destructive hover:underline"
        >
          Clear stored credential
        </button>
      )}
      {cleared && (
        <span className="text-[12px] text-muted-foreground">
          Credential will be removed on save.
        </span>
      )}
    </div>
  );
}
