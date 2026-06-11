import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import { type Editor, EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  Bold,
  Code,
  Italic,
  Link as LinkIcon,
  List,
  ListOrdered,
  Quote,
  Redo2,
  SquareCode,
  Strikethrough,
  Undo2,
} from "@/components/icons";
import { useEffect } from "react";
import { Markdown } from "tiptap-markdown";
import { cn } from "@/lib/utils";

/**
 * Rich-text note editor (TipTap / ProseMirror) that reads and writes Markdown.
 *
 * Notes are stored as a Markdown string (rendered everywhere with the shared
 * `Markdown` component), so this editor serializes to Markdown on every change
 * and hydrates from Markdown on external resets (e.g. clearing after a post).
 * Cmd/Ctrl+Enter submits; plain Enter inserts a newline (Jira-style).
 */
export function NoteEditor({
  value,
  onChange,
  onSubmit,
  onPasteFiles,
  placeholder = "Add a note…",
  disabled = false,
  autoFocus = false,
  attachments,
  footer,
}: {
  value: string;
  onChange: (markdown: string) => void;
  onSubmit?: () => void;
  onPasteFiles?: (files: File[]) => void;
  placeholder?: string;
  disabled?: boolean;
  /** Focus the editor on mount (used by the expand-on-click composer). */
  autoFocus?: boolean;
  /** Slot rendered between the editor body and the action bar (e.g. chips). */
  attachments?: React.ReactNode;
  /** Action bar slot rendered at the bottom (e.g. attach + post buttons). */
  footer?: React.ReactNode;
}) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
      Link.configure({ openOnClick: false, autolink: true }),
      Placeholder.configure({ placeholder }),
      Markdown.configure({ html: false, linkify: true, transformPastedText: true }),
    ],
    content: value,
    autofocus: autoFocus ? "end" : false,
    editable: !disabled,
    editorProps: {
      attributes: {
        class: "prose-chat max-w-none px-3 py-2 text-sm",
      },
      handleKeyDown: (_view, event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          onSubmit?.();
          return true;
        }
        return false;
      },
      handlePaste: (_view, event) => {
        const files = Array.from(event.clipboardData?.files ?? []);
        if (files.length && onPasteFiles) {
          onPasteFiles(files);
          return true;
        }
        return false;
      },
    },
    onUpdate: ({ editor }) => {
      onChange(editor.storage.markdown.getMarkdown());
    },
  });

  // Hydrate from external value changes (clear after post, programmatic set)
  // without re-emitting an update (would loop with onChange).
  useEffect(() => {
    if (!editor) return;
    const current = editor.storage.markdown.getMarkdown();
    if (value !== current) {
      editor.commands.setContent(value, false);
    }
  }, [value, editor]);

  useEffect(() => {
    editor?.setEditable(!disabled);
  }, [disabled, editor]);

  return (
    <div className="note-editor w-full overflow-hidden rounded border border-input bg-card transition-colors duration-100 hover:border-border-strong focus-within:border-[#4C9AFF] focus-within:hover:border-[#4C9AFF]">
      <Toolbar editor={editor} />
      <EditorContent editor={editor} />
      {attachments}
      {footer && (
        <div className="flex items-center justify-between gap-2 border-t border-border px-2 py-1.5">
          {footer}
        </div>
      )}
    </div>
  );
}

function Toolbar({ editor }: { editor: Editor | null }) {
  if (!editor) return null;
  const setLink = () => {
    const prev = editor.getAttributes("link").href as string | undefined;
    const url = window.prompt("Link URL", prev ?? "https://");
    if (url === null) return;
    if (url === "") {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run();
  };

  return (
    <div className="flex flex-wrap items-center gap-0.5 border-b border-border px-1.5 py-1">
      <Btn
        active={editor.isActive("bold")}
        onClick={() => editor.chain().focus().toggleBold().run()}
        title="Bold (Ctrl+B)"
      >
        <Bold className="h-3.5 w-3.5" />
      </Btn>
      <Btn
        active={editor.isActive("italic")}
        onClick={() => editor.chain().focus().toggleItalic().run()}
        title="Italic (Ctrl+I)"
      >
        <Italic className="h-3.5 w-3.5" />
      </Btn>
      <Btn
        active={editor.isActive("strike")}
        onClick={() => editor.chain().focus().toggleStrike().run()}
        title="Strikethrough"
      >
        <Strikethrough className="h-3.5 w-3.5" />
      </Btn>
      <Btn
        active={editor.isActive("code")}
        onClick={() => editor.chain().focus().toggleCode().run()}
        title="Inline code"
      >
        <Code className="h-3.5 w-3.5" />
      </Btn>
      <Sep />
      <Btn
        active={editor.isActive("heading", { level: 2 })}
        onClick={() =>
          editor.chain().focus().toggleHeading({ level: 2 }).run()
        }
        title="Heading"
      >
        <span className="text-xs font-bold">H</span>
      </Btn>
      <Btn
        active={editor.isActive("bulletList")}
        onClick={() => editor.chain().focus().toggleBulletList().run()}
        title="Bullet list"
      >
        <List className="h-3.5 w-3.5" />
      </Btn>
      <Btn
        active={editor.isActive("orderedList")}
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
        title="Numbered list"
      >
        <ListOrdered className="h-3.5 w-3.5" />
      </Btn>
      <Btn
        active={editor.isActive("blockquote")}
        onClick={() => editor.chain().focus().toggleBlockquote().run()}
        title="Quote"
      >
        <Quote className="h-3.5 w-3.5" />
      </Btn>
      <Btn
        active={editor.isActive("codeBlock")}
        onClick={() => editor.chain().focus().toggleCodeBlock().run()}
        title="Code block"
      >
        <SquareCode className="h-3.5 w-3.5" />
      </Btn>
      <Btn active={editor.isActive("link")} onClick={setLink} title="Link">
        <LinkIcon className="h-3.5 w-3.5" />
      </Btn>
      <Sep />
      <Btn
        onClick={() => editor.chain().focus().undo().run()}
        disabled={!editor.can().undo()}
        title="Undo"
      >
        <Undo2 className="h-3.5 w-3.5" />
      </Btn>
      <Btn
        onClick={() => editor.chain().focus().redo().run()}
        disabled={!editor.can().redo()}
        title="Redo"
      >
        <Redo2 className="h-3.5 w-3.5" />
      </Btn>
    </div>
  );
}

function Btn({
  active,
  disabled,
  onClick,
  title,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      aria-pressed={active}
      className={cn(
        "flex h-7 w-7 items-center justify-center rounded transition-colors disabled:opacity-40",
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function Sep() {
  return <span className="mx-0.5 h-4 w-px bg-border" />;
}
