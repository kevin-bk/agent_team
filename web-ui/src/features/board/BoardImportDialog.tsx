import { FileText } from "@/components/icons";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  useImportBoardTasksCsv,
  usePreviewBoardTasksCsv,
} from "@/api/hooks";
import type { CsvImportPreview } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

const ACTION_STYLE: Record<string, string> = {
  create: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  update: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  error: "bg-red-500/15 text-red-600 dark:text-red-400",
};

/**
 * Import tasks from a CSV file. Mirrors the Jira import flow: the user picks a
 * file, sees a dry-run preview (create / update / error per row), then confirms.
 * Only `title` is required; everything else is optional with safe defaults.
 */
export function BoardImportDialog({
  boardId,
  open,
  onClose,
}: {
  boardId: string;
  open: boolean;
  onClose: () => void;
}) {
  const preview = usePreviewBoardTasksCsv(boardId);
  const apply = useImportBoardTasksCsv(boardId);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<CsvImportPreview | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setFile(null);
      setResult(null);
      preview.reset();
      apply.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const pickFile = async (next: File | null) => {
    setFile(next);
    setResult(null);
    if (!next) return;
    try {
      setResult(await preview.mutateAsync(next));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not read the CSV");
    }
  };

  const importable = (result?.creates ?? 0) + (result?.updates ?? 0);

  const runImport = async () => {
    if (!file || importable === 0) return;
    try {
      const res = await apply.mutateAsync(file);
      toast.success(
        `Imported: ${res.created} created, ${res.updated} updated` +
          (res.skipped ? `, ${res.skipped} skipped` : ""),
      );
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Import failed");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        className="max-w-2xl"
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Import tasks from CSV</DialogTitle>
        </DialogHeader>

        <div className="grid gap-2 pt-1">
          <span className="text-[12.5px] text-muted-foreground/80">
            The file needs a <code className="text-foreground">title</code> column.
            Optional columns: <code>human_key</code>, <code>description</code>,{" "}
            <code>task_type</code>, <code>status</code>, <code>priority</code>,{" "}
            <code>labels</code> (<code>;</code>-separated),{" "}
            <code>assignee_email</code>, <code>jira_key</code>,{" "}
            <code>archived</code>. A matching <code>human_key</code> updates that
            task; otherwise a new one is created.
          </span>

          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            className="block w-full text-[12.5px] text-muted-foreground file:mr-3 file:rounded file:border-0 file:bg-surface-3 file:px-3 file:py-1.5 file:text-[12.5px] file:font-medium file:text-foreground hover:file:bg-surface-2"
          />

          {preview.isPending && (
            <div className="flex items-center gap-1.5 py-2 text-xs text-muted-foreground">
              <Spinner className="h-3 w-3" /> reading…
            </div>
          )}

          {result && (
            <>
              <div className="flex flex-wrap gap-2 pt-1 text-[12px]">
                <Pill label={`${result.creates} create`} kind="create" />
                <Pill label={`${result.updates} update`} kind="update" />
                {result.errors > 0 && (
                  <Pill label={`${result.errors} error`} kind="error" />
                )}
                <span className="self-center text-muted-foreground">
                  {result.total} row{result.total === 1 ? "" : "s"} total
                </span>
              </div>

              <div className="mt-1 max-h-[45vh] overflow-y-auto rounded border border-border">
                <table className="w-full text-[12.5px]">
                  <thead className="sticky top-0 bg-surface-2 text-left text-muted-foreground">
                    <tr>
                      <th className="px-2 py-1.5 font-medium">#</th>
                      <th className="px-2 py-1.5 font-medium">Action</th>
                      <th className="px-2 py-1.5 font-medium">Title</th>
                      <th className="px-2 py-1.5 font-medium">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((r) => (
                      <tr key={r.line} className="border-t border-border">
                        <td className="px-2 py-1.5 text-muted-foreground">
                          {r.line}
                        </td>
                        <td className="px-2 py-1.5">
                          <span
                            className={cn(
                              "rounded px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.04em]",
                              ACTION_STYLE[r.action],
                            )}
                          >
                            {r.action}
                          </span>
                          {r.human_key && (
                            <span className="ml-1.5 text-[11px] text-muted-foreground">
                              {r.human_key}
                            </span>
                          )}
                        </td>
                        <td className="max-w-[16rem] truncate px-2 py-1.5 text-foreground">
                          {r.title || <span className="text-muted-foreground">—</span>}
                        </td>
                        <td className="px-2 py-1.5 text-muted-foreground">
                          {r.message}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={onClose} disabled={apply.isPending}>
            Cancel
          </Button>
          <Button
            onClick={runImport}
            disabled={apply.isPending || importable === 0}
          >
            {apply.isPending ? (
              <>
                <Spinner className="h-3.5 w-3.5" /> Importing…
              </>
            ) : (
              <>
                <FileText className="h-4 w-4" /> Import {importable || ""} task
                {importable === 1 ? "" : "s"}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Pill({ label, kind }: { label: string; kind: string }) {
  return (
    <span className={cn("rounded px-2 py-0.5 font-medium", ACTION_STYLE[kind])}>
      {label}
    </span>
  );
}
