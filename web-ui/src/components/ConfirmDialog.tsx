import { AlertTriangle } from "@/components/icons";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

export interface ConfirmOptions {
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** "danger" paints the confirm button destructive + shows a warning icon. */
  tone?: "default" | "danger";
}

export interface PromptOptions {
  title: string;
  description?: ReactNode;
  defaultValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;
type PromptFn = (opts: PromptOptions) => Promise<string | null>;

interface ConfirmApi {
  confirm: ConfirmFn;
  prompt: PromptFn;
}

type Request =
  | { kind: "confirm"; opts: ConfirmOptions }
  | { kind: "prompt"; opts: PromptOptions };

const ConfirmContext = createContext<ConfirmApi | null>(null);

/**
 * Promise-based replacement for the native ``window.confirm`` and
 * ``window.prompt``. Wrap the app once with ``<ConfirmProvider>`` then call
 * ``await confirm({...})`` or ``await prompt({...})`` anywhere.
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [req, setReq] = useState<Request | null>(null);
  const [value, setValue] = useState("");
  const resolver = useRef<((value: boolean | string | null) => void) | null>(
    null,
  );

  const confirm = useCallback<ConfirmFn>((opts) => {
    setReq({ kind: "confirm", opts });
    setOpen(true);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve as (v: boolean | string | null) => void;
    });
  }, []);

  const prompt = useCallback<PromptFn>((opts) => {
    setReq({ kind: "prompt", opts });
    setValue(opts.defaultValue ?? "");
    setOpen(true);
    return new Promise<string | null>((resolve) => {
      resolver.current = resolve as (v: boolean | string | null) => void;
    });
  }, []);

  const settle = useCallback((result: boolean | string | null) => {
    setOpen(false);
    resolver.current?.(result);
    resolver.current = null;
  }, []);

  const isPrompt = req?.kind === "prompt";
  const danger = req?.kind === "confirm" && req.opts.tone === "danger";

  const onAccept = () => settle(isPrompt ? value : true);
  const onCancel = () => settle(isPrompt ? null : false);

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onAccept();
    }
  };

  return (
    <ConfirmContext.Provider value={{ confirm, prompt }}>
      {children}
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) onCancel();
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {danger && (
                <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
              )}
              {req?.opts.title}
            </DialogTitle>
            {req?.opts.description != null && (
              <DialogDescription>{req.opts.description}</DialogDescription>
            )}
          </DialogHeader>
          {isPrompt && (
            <PromptInput
              value={value}
              placeholder={req.opts.placeholder}
              onChange={setValue}
              onKeyDown={onKeyDown}
            />
          )}
          <DialogFooter>
            <Button variant="secondary" onClick={onCancel}>
              {req?.opts.cancelLabel ?? "Cancel"}
            </Button>
            <Button
              variant={danger ? "destructive" : "default"}
              onClick={onAccept}
              disabled={isPrompt && !value.trim()}
            >
              {req?.opts.confirmLabel ?? (isPrompt ? "Save" : "Confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ConfirmContext.Provider>
  );
}

function PromptInput({
  value,
  placeholder,
  onChange,
  onKeyDown,
}: {
  value: string;
  placeholder?: string;
  onChange: (v: string) => void;
  onKeyDown: (e: KeyboardEvent<HTMLInputElement>) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      ref.current?.focus();
      ref.current?.select();
    });
    return () => cancelAnimationFrame(id);
  }, []);
  return (
    <Input
      ref={ref}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={onKeyDown}
    />
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used within <ConfirmProvider>");
  return ctx.confirm;
}

export function usePrompt(): PromptFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("usePrompt must be used within <ConfirmProvider>");
  return ctx.prompt;
}
