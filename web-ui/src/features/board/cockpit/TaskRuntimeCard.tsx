import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  Circle,
  CircleDot,
  Cpu,
  Loader2,
  Moon,
  Play,
  TerminalSquare,
  Trash2,
} from "@/components/icons";
import {
  useControlTaskRuntime,
  useExecTaskRuntime,
  useTaskRuntime,
} from "@/api/hooks";
import { useConfirm } from "@/components/ConfirmDialog";
import { SandboxConsoleDialog } from "@/features/sandboxes/SandboxConsoleDialog";
import { cn } from "@/lib/utils";

/**
 * "Runtime" card in the task cockpit: shows where this task executes — on the
 * host or in an isolated OpenSandbox environment — plus the effective profile
 * (image / resources / idle timeout) and the live sandbox state when one is
 * currently warm. Editors can manually run/resume, pause or kill the sandbox,
 * but only while no agent is running so a live turn is never changed mid-flight.
 * The profile itself is set via env defaults or the board's
 * ``runtime_profile`` override (Board settings → Isolated runtime).
 */
export function TaskRuntimeCard({
  taskId,
  canControl = false,
  busy = false,
}: {
  taskId: string;
  /** Whether the viewer may pause/kill the sandbox (editor+). */
  canControl?: boolean;
  /** True while an agent is running/queued on the task — controls are locked. */
  busy?: boolean;
}) {
  const runtime = useTaskRuntime(taskId);
  const control = useControlTaskRuntime(taskId);
  const execRuntime = useExecTaskRuntime(taskId);
  const confirm = useConfirm();
  const [consoleOpen, setConsoleOpen] = useState(false);
  const rt = runtime.data;
  if (runtime.isLoading || !rt) return null;

  const isolated = rt.isolated;
  const state = rt.sandbox_state; // running | paused | null (cold)
  const dot = stateColor(isolated, state);
  // Something to act on only when a sandbox is warm (running) or suspended.
  const hasSandbox = state === "running" || state === "paused";
  const showControls = isolated && canControl;
  const locked = busy || control.isPending;

  const act = async (action: "run" | "pause" | "kill") => {
    if (action !== "run") {
      const ok = await confirm(
        action === "pause"
          ? {
              title: "Pause sandbox?",
              description:
                "Suspends this task's sandbox to free resources. It resumes automatically on the next agent turn.",
              confirmLabel: "Pause",
            }
          : {
              title: "Kill sandbox?",
              description:
                "Tears down this task's sandbox entirely. A fresh environment is provisioned from scratch on the next turn.",
              confirmLabel: "Kill",
              tone: "danger",
            },
      );
      if (!ok) return;
    }
    try {
      await control.mutateAsync(action);
      toast.success(
        action === "run"
          ? "Sandbox running"
          : action === "pause"
            ? "Sandbox paused"
            : "Sandbox killed",
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `Failed to ${action} sandbox`);
    }
  };

  return (
    <div className="border-b border-border">
      <div className="flex items-center gap-2 px-4 py-2.5">
        <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-[13px] font-semibold text-foreground">Runtime</span>
        <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
          {state === "running" ? (
            <CircleDot className={cn("h-3.5 w-3.5", dot)} />
          ) : (
            <Circle className={cn("h-3.5 w-3.5", dot)} />
          )}
          {stateLabel(isolated, state)}
        </span>
      </div>
      <div className="grid gap-1 px-4 pb-3 text-[12px]">
        <Row label="Provider" value={isolated ? "OpenSandbox (isolated)" : "Host (local)"} />
        {isolated && <Row label="Mode" value={strategyLabel(rt.strategy)} />}
        {isolated && rt.image && <Row label="Image" value={rt.image} mono />}
        {isolated && (
          <>
            <Row
              label="Resources"
              value={`${rt.cpu} vCPU · ${formatMem(rt.memory_mb)}`}
            />
            <Row label="Workspace" value={rt.workspace_mode} />
            <Row label="Idle timeout" value={`${rt.idle_timeout_minutes} min`} />
            {rt.strict_isolation && (
              <Row label="Isolation" value="strict (no host fallback)" />
            )}
            {rt.sandbox_id && <Row label="Sandbox" value={rt.sandbox_id} mono />}
          </>
        )}
        {showControls && (
          <div className="mt-2 flex items-center gap-2">
            {state !== "running" && (
              <ControlButton
                icon={
                  control.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Play className="h-3.5 w-3.5" />
                  )
                }
                label={state === "paused" ? "Resume" : "Run"}
                disabled={locked}
                title={
                  busy
                    ? "An agent is running — stop it first"
                    : state === "paused"
                      ? "Resume sandbox"
                      : "Create and run sandbox"
                }
                onClick={() => act("run")}
              />
            )}
            {state === "running" && (
              <ControlButton
                icon={<TerminalSquare className="h-3.5 w-3.5" />}
                label="Console"
                disabled={control.isPending}
                title="Run a one-off command inside this task's sandbox"
                onClick={() => setConsoleOpen(true)}
              />
            )}
            {state === "running" && (
              <ControlButton
                icon={
                  control.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Moon className="h-3.5 w-3.5" />
                  )
                }
                label="Pause"
                disabled={locked}
                title={busy ? "An agent is running — stop it first" : "Pause sandbox"}
                onClick={() => act("pause")}
              />
            )}
            {hasSandbox && (
              <ControlButton
                icon={<Trash2 className="h-3.5 w-3.5" />}
                label="Kill"
                danger
                disabled={locked}
                title={busy ? "An agent is running — stop it first" : "Kill sandbox"}
                onClick={() => act("kill")}
              />
            )}
            {busy && (
              <span className="text-[11px] text-muted-foreground">
                locked while running
              </span>
            )}
          </div>
        )}
      </div>
      <SandboxConsoleDialog
        open={consoleOpen}
        onOpenChange={setConsoleOpen}
        title="Sandbox console"
        subtitle={rt.sandbox_id ?? undefined}
        exec={(command) => execRuntime.mutateAsync({ command })}
      />
    </div>
  );
}

function ControlButton({
  icon,
  label,
  onClick,
  disabled,
  title,
  danger,
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[12px] font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-40",
        danger
          ? "text-destructive hover:bg-destructive/10"
          : "text-foreground hover:bg-surface-3",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="w-24 shrink-0 text-muted-foreground">{label}</span>
      <span
        className={cn(
          "min-w-0 flex-1 truncate text-foreground",
          mono && "font-mono text-[11px]",
        )}
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

function strategyLabel(strategy: string | null): string {
  if (strategy === "acp_sidecar") return "ACP sidecar (full)";
  if (strategy === "oneshot") return "one-shot CLI";
  return strategy ?? "—";
}

function formatMem(mb: number): string {
  if (mb >= 1024 && mb % 1024 === 0) return `${mb / 1024} GB`;
  return `${mb} MB`;
}

function stateColor(isolated: boolean, state: string | null): string {
  if (!isolated) return "text-muted-foreground";
  if (state === "running") return "text-emerald-500";
  if (state === "paused") return "text-amber-500";
  return "text-muted-foreground";
}

function stateLabel(isolated: boolean, state: string | null): string {
  if (!isolated) return "host";
  if (state === "running") return "running";
  if (state === "paused") return "paused";
  return "idle";
}
