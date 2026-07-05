import { useEffect, useRef, useState } from "react";
import { Loader2 } from "@/components/icons";
import type { SandboxExecResultDTO } from "@/api/types";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface HistoryEntry {
  id: number;
  command: string;
  result?: SandboxExecResultDTO;
  error?: string;
}

/**
 * One-off command console for a live sandbox — shared by the task cockpit and
 * the admin Sandboxes page. NOT an interactive shell (no stdin/PTY, no cwd
 * persistence between commands): each command runs fresh via the sandbox exec
 * API and its captured stdout/stderr is shown. Meant for quick debugging like
 * `which playwright`, `env | grep MCP`, `ls /workspace`.
 */
export function SandboxConsoleDialog({
  open,
  onOpenChange,
  title,
  subtitle,
  exec,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** e.g. "Sandbox console — T-93" */
  title: string;
  /** e.g. the sandbox id or image, shown under the title. */
  subtitle?: string;
  /** Runs a command in the target sandbox and resolves with its result. */
  exec: (command: string) => Promise<SandboxExecResultDTO>;
}) {
  const [command, setCommand] = useState("");
  const [running, setRunning] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const nextId = useRef(1);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Autoscroll to the latest output; refocus the input after each run.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [history]);
  useEffect(() => {
    if (open && !running) inputRef.current?.focus();
  }, [open, running]);

  const run = async () => {
    const cmd = command.trim();
    if (!cmd || running) return;
    const id = nextId.current++;
    setHistory((h) => [...h, { id, command: cmd }]);
    setCommand("");
    setRunning(true);
    try {
      const result = await exec(cmd);
      setHistory((h) => h.map((e) => (e.id === id ? { ...e, result } : e)));
    } catch (err) {
      const message = err instanceof Error ? err.message : "command failed";
      setHistory((h) => h.map((e) => (e.id === id ? { ...e, error: message } : e)));
    } finally {
      setRunning(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl gap-3">
        <DialogHeader>
          <DialogTitle className="text-base">{title}</DialogTitle>
          <DialogDescription>
            {subtitle ? <span className="font-mono text-xs">{subtitle}</span> : null}
            {subtitle ? " — " : ""}
            One-off commands (no interactive shell; 120s max per command).
          </DialogDescription>
        </DialogHeader>

        <div
          ref={scrollRef}
          className="h-80 overflow-y-auto rounded-md border border-border bg-zinc-950 p-3 font-mono text-[12px] leading-relaxed text-zinc-100"
        >
          {history.length === 0 ? (
            <div className="text-zinc-500">
              Type a command below — e.g.{" "}
              <span className="text-zinc-300">which playwright</span>,{" "}
              <span className="text-zinc-300">env | sort</span>,{" "}
              <span className="text-zinc-300">ls /workspace</span>
            </div>
          ) : (
            history.map((entry) => (
              <div key={entry.id} className="mb-3 last:mb-0">
                <div className="flex items-baseline gap-2">
                  <span className="select-none text-emerald-400">$</span>
                  <span className="whitespace-pre-wrap break-all text-zinc-100">
                    {entry.command}
                  </span>
                </div>
                {!entry.result && !entry.error ? (
                  <div className="mt-1 flex items-center gap-1.5 text-zinc-500">
                    <Loader2 className="h-3 w-3 animate-spin" /> running…
                  </div>
                ) : entry.error ? (
                  <pre className="mt-1 whitespace-pre-wrap break-all text-red-400">
                    {entry.error}
                  </pre>
                ) : (
                  <>
                    {entry.result!.stdout ? (
                      <pre className="mt-1 whitespace-pre-wrap break-all text-zinc-200">
                        {entry.result!.stdout}
                      </pre>
                    ) : null}
                    {entry.result!.stderr ? (
                      <pre className="mt-1 whitespace-pre-wrap break-all text-amber-300">
                        {entry.result!.stderr}
                      </pre>
                    ) : null}
                    <div
                      className={cn(
                        "mt-1 text-[11px]",
                        entry.result!.exit_code === 0
                          ? "text-zinc-500"
                          : "text-red-400",
                      )}
                    >
                      exit {entry.result!.exit_code} ·{" "}
                      {(entry.result!.duration_ms / 1000).toFixed(1)}s
                      {entry.result!.timed_out ? " · TIMED OUT" : ""}
                      {entry.result!.truncated ? " · output truncated" : ""}
                    </div>
                  </>
                )}
              </div>
            ))
          )}
        </div>

        <div className="flex items-center gap-2">
          <span className="select-none font-mono text-sm text-emerald-500">$</span>
          <input
            ref={inputRef}
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void run();
              }
            }}
            disabled={running}
            placeholder="command to run in the sandbox…"
            spellCheck={false}
            autoComplete="off"
            className="h-9 flex-1 rounded-md border border-border bg-transparent px-2 font-mono text-[13px] outline-none focus:ring-2 focus:ring-ring disabled:opacity-60"
          />
          <Button size="sm" onClick={() => void run()} disabled={running || !command.trim()}>
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            Run
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
