import { useEffect, useState } from "react";
import { toast } from "sonner";
import { useUpdateBoard } from "@/api/hooks";
import type { BoardDTO, PatchBoardBody } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

//: Common Jira issue type names offered as filter chips.
const JIRA_ISSUE_TYPES = ["Epic", "Story", "Task", "Bug", "Sub-task"];
//: Jira's universal status categories (project-agnostic).
const JIRA_STATUS_CATEGORIES = ["To Do", "In Progress", "Done"];
const UPDATED_WINDOWS: { label: string; days: number }[] = [
  { label: "Any time", days: 0 },
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
];

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2.5 py-1 text-[12px] font-medium transition-colors",
        active
          ? "border-primary bg-primary/10 text-primary"
          : "border-border bg-surface-1 text-muted-foreground hover:bg-surface-3",
      )}
    >
      {children}
    </button>
  );
}

/**
 * Per-board Jira connection settings + import filter. The API token is
 * write-only: it's never returned by the server, so the field stays blank and is
 * only sent when the user types a new one. "Import from Jira" saves first, then
 * opens the preview dialog that pulls the project's issues (narrowed by the
 * filter below so the preview stays small).
 */
export function BoardJiraDialog({
  board,
  open,
  onClose,
  onSyncAll,
}: {
  board: BoardDTO;
  open: boolean;
  onClose: () => void;
  /** Called after the config is saved, to open the import preview dialog. */
  onSyncAll: () => void;
}) {
  const update = useUpdateBoard(board.id);

  const [enabled, setEnabled] = useState(board.jira_enabled ?? false);
  const [baseUrl, setBaseUrl] = useState(board.jira_base_url ?? "");
  const [email, setEmail] = useState(board.jira_email ?? "");
  const [projectKey, setProjectKey] = useState(board.jira_project_key ?? "");
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const hasToken = board.jira_has_token ?? false;

  // Jira-side import filter (narrows the project query → smaller preview).
  const [issueTypes, setIssueTypes] = useState<string[]>([]);
  const [statusCategories, setStatusCategories] = useState<string[]>([]);
  const [updatedWithin, setUpdatedWithin] = useState(0);

  useEffect(() => {
    if (!open) return;
    setEnabled(board.jira_enabled ?? false);
    setBaseUrl(board.jira_base_url ?? "");
    setEmail(board.jira_email ?? "");
    setProjectKey(board.jira_project_key ?? "");
    setToken("");
    setClearToken(false);
    const f = board.jira_sync_filter ?? {};
    setIssueTypes(f.issue_types ?? []);
    setStatusCategories(f.status_categories ?? []);
    setUpdatedWithin(f.updated_within_days ?? 0);
  }, [open, board]);

  const toggle = (value: string, list: string[], set: (v: string[]) => void) =>
    set(list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);

  const buildBody = (): PatchBoardBody => {
    const body: PatchBoardBody = {
      jira_enabled: enabled,
      jira_base_url: baseUrl.trim() || null,
      jira_email: email.trim() || null,
      jira_project_key: projectKey.trim() || null,
      jira_sync_filter: {
        issue_types: issueTypes,
        status_categories: statusCategories,
        updated_within_days: updatedWithin || null,
      },
    };
    if (clearToken) body.jira_api_token = "";
    else if (token.trim()) body.jira_api_token = token.trim();
    return body;
  };

  const save = async () => {
    try {
      await update.mutateAsync(buildBody());
      toast.success("Jira settings saved");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save Jira settings");
    }
  };

  const syncAll = async () => {
    try {
      // Persist config first so the preview uses the latest project/credentials.
      await update.mutateAsync(buildBody());
      onSyncAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save settings");
    }
  };

  const busy = update.isPending;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Jira sync</DialogTitle>
        </DialogHeader>

        <div className="grid max-h-[65vh] gap-4 overflow-y-auto pr-1 pt-1">
          <label className="flex cursor-pointer items-center gap-2.5">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="h-3.5 w-3.5 accent-primary"
            />
            <span className="text-[13px] font-medium text-foreground">
              Enable Jira sync for this board
            </span>
          </label>

          <label className="grid gap-1.5">
            <span className="text-[13px] font-medium text-muted-foreground">
              Site URL
            </span>
            <Input
              value={baseUrl}
              placeholder="https://your-domain.atlassian.net"
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </label>

          <div className="grid grid-cols-2 gap-4">
            <label className="grid gap-1.5">
              <span className="text-[13px] font-medium text-muted-foreground">
                Account email
              </span>
              <Input
                value={email}
                placeholder="svc@company.com"
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <label className="grid gap-1.5">
              <span className="text-[13px] font-medium text-muted-foreground">
                Project key
              </span>
              <Input
                value={projectKey}
                placeholder="ABC"
                onChange={(e) => setProjectKey(e.target.value)}
              />
            </label>
          </div>

          <div className="grid gap-1.5">
            <span className="flex items-center justify-between text-[13px] font-medium text-muted-foreground">
              API token
              {hasToken && !clearToken && (
                <button
                  type="button"
                  onClick={() => setClearToken(true)}
                  className="text-[12px] font-medium text-destructive hover:underline"
                >
                  Clear stored token
                </button>
              )}
            </span>
            <Input
              type="password"
              value={clearToken ? "" : token}
              disabled={clearToken}
              placeholder={
                clearToken
                  ? "Token will be removed on save"
                  : hasToken
                    ? "•••••••• (stored — leave blank to keep)"
                    : "Paste your Jira API token"
              }
              onChange={(e) => setToken(e.target.value)}
            />
            <span className="text-[12px] text-muted-foreground/80">
              Create one at{" "}
              <a
                href="https://id.atlassian.com/manage-profile/security/api-tokens"
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-primary hover:underline"
              >
                id.atlassian.com → Security → API tokens
              </a>
              . Stored on the server and never shown again.
            </span>
          </div>

          {/* ── Import filter (Jira-side, narrows the preview) ─────────── */}
          <div className="grid gap-2.5 border-t border-border pt-3">
            <span className="text-[13px] font-semibold text-foreground">
              Import filter
            </span>
            <span className="text-[12px] text-muted-foreground/80">
              Narrows the issues pulled from project{" "}
              <span className="font-medium text-foreground">
                {projectKey.trim() || "—"}
              </span>{" "}
              so the preview stays small. Leave a group empty to not restrict by it.
            </span>

            <div className="grid gap-1.5">
              <span className="text-[12px] font-medium text-muted-foreground">
                Issue type
              </span>
              <div className="flex flex-wrap gap-1.5">
                {JIRA_ISSUE_TYPES.map((t) => (
                  <Chip
                    key={t}
                    active={issueTypes.includes(t)}
                    onClick={() => toggle(t, issueTypes, setIssueTypes)}
                  >
                    {t}
                  </Chip>
                ))}
              </div>
            </div>

            <div className="grid gap-1.5">
              <span className="text-[12px] font-medium text-muted-foreground">
                Status
              </span>
              <div className="flex flex-wrap gap-1.5">
                {JIRA_STATUS_CATEGORIES.map((c) => (
                  <Chip
                    key={c}
                    active={statusCategories.includes(c)}
                    onClick={() => toggle(c, statusCategories, setStatusCategories)}
                  >
                    {c}
                  </Chip>
                ))}
              </div>
            </div>

            <div className="grid gap-1.5">
              <span className="text-[12px] font-medium text-muted-foreground">
                Updated within
              </span>
              <div className="flex flex-wrap gap-1.5">
                {UPDATED_WINDOWS.map((w) => (
                  <Chip
                    key={w.days}
                    active={updatedWithin === w.days}
                    onClick={() => setUpdatedWithin(w.days)}
                  >
                    {w.label}
                  </Chip>
                ))}
              </div>
            </div>
          </div>
        </div>

        <DialogFooter className="sm:justify-between">
          <Button
            variant="secondary"
            onClick={syncAll}
            disabled={busy || !enabled || !projectKey.trim()}
            title={
              enabled
                ? "Save settings and pick issues to import"
                : "Enable Jira sync first"
            }
          >
            Import from Jira
          </Button>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={save} disabled={busy}>
              Save
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
