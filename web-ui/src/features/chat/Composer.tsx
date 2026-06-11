import { ChevronDown, Paperclip, Send, Square } from "@/components/icons";
import {
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  useRef,
  useState,
} from "react";
import type { AttachmentDTO } from "@/api/types";
import {
  AttachmentChips,
  usePendingAttachments,
} from "@/components/attachments";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { UserAttachment } from "./types";

export function Composer({
  running,
  onSend,
  onSteer,
  onCancel,
  onUpload,
  onDeleteUpload,
}: {
  running: boolean;
  onSend: (text: string, attachments: UserAttachment[]) => void;
  onSteer: (text: string, mode: "queue" | "interrupt" | "steer") => void;
  onCancel: () => void;
  onUpload?: (files: File[]) => Promise<AttachmentDTO[]>;
  onDeleteUpload?: (dto: AttachmentDTO) => Promise<unknown> | void;
}) {
  const [text, setText] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const att = usePendingAttachments(
    onUpload ?? (async () => []),
    onDeleteUpload,
  );

  const submit = () => {
    const value = text.trim();
    const attachments = att.toUserAttachments();
    if (running) {
      if (!value) return;
      onSteer(value, "queue");
      setText("");
      return;
    }
    if (!value && attachments.length === 0) return;
    if (att.uploading) return;
    onSend(value, attachments);
    setText("");
    att.clear();
  };

  const pickFiles = (files: FileList | null) => {
    if (!files || !files.length || !onUpload) return;
    void att.addFiles(Array.from(files));
  };

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    if (!onUpload) return;
    const files = Array.from(e.clipboardData.files);
    if (files.length) {
      e.preventDefault();
      void att.addFiles(files);
    }
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    if (!onUpload) return;
    const files = Array.from(e.dataTransfer.files);
    if (files.length) void att.addFiles(files);
  };

  const steerWith = (mode: "queue" | "interrupt" | "steer") => {
    const value = text.trim();
    if (!value) return;
    onSteer(value, mode);
    setText("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="border-t border-border bg-background/80 backdrop-blur">
      <div className="mx-auto w-full max-w-3xl px-4 py-3">
        {onUpload && (
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              pickFiles(e.target.files);
              e.target.value = "";
            }}
          />
        )}
        {/* Single framed composer (same pattern as the notes editor):
            chips + textarea + bottom action bar inside one bordered box. */}
        <div
          onDragOver={(e) => {
            if (!onUpload) return;
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={cn(
            "overflow-hidden rounded border border-input bg-card transition-colors duration-100 hover:border-border-strong focus-within:border-[#4C9AFF] focus-within:hover:border-[#4C9AFF]",
            dragOver && "ring-2 ring-primary",
          )}
        >
          <AttachmentChips
            items={att.items}
            onRemove={att.remove}
            className="px-3 pt-2"
          />
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            rows={1}
            placeholder={
              running
                ? "Steer the running agent… (Enter to queue)"
                : "Message the agent…"
            }
            className="max-h-48 min-h-[40px] resize-none rounded-none border-0 bg-transparent shadow-none focus-visible:border-transparent focus-visible:ring-0"
          />
          <div className="flex items-center justify-between gap-2 border-t border-border px-2 py-1.5">
            {onUpload ? (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => fileRef.current?.click()}
                title="Attach files or images"
                disabled={running}
                className="shrink-0 text-muted-foreground hover:text-primary"
              >
                <Paperclip className="h-4 w-4" />
              </Button>
            ) : (
              <span />
            )}
            {running ? (
              <div className="flex items-center gap-1">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={!text.trim()}
                    >
                      Steer <ChevronDown className="h-3.5 w-3.5" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onSelect={() => steerWith("queue")}>
                      Queue (next checkpoint)
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => steerWith("steer")}>
                      Steer (inject now)
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => steerWith("interrupt")}>
                      Interrupt (cancel turn)
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <Button
                  variant="destructive"
                  size="icon"
                  onClick={onCancel}
                  title="Stop the run"
                >
                  <Square className="h-4 w-4" />
                </Button>
              </div>
            ) : (
              <Button
                size="sm"
                onClick={submit}
                disabled={att.uploading || (!text.trim() && !att.hasReady)}
              >
                <Send className="h-3.5 w-3.5" /> Send
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
