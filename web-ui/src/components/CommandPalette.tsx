import { Command } from "cmdk";
import { LayoutGrid, MessagesSquare, Search } from "@/components/icons";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useBoards } from "@/api/hooks";

/**
 * Global command palette (⌘K / Ctrl-K). Fuzzy-jump to boards and the main
 * sections without leaving the keyboard. cmdk handles filtering + a11y; we
 * just supply the items and wire navigation.
 */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const boards = useBoards();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const run = (action: () => void) => {
    setOpen(false);
    action();
  };

  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command menu"
      overlayClassName="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm"
      contentClassName="fixed left-1/2 top-[15%] z-50 w-[92vw] max-w-xl -translate-x-1/2 overflow-hidden rounded-lg border border-border bg-popover text-popover-foreground shadow-overlay"
    >
      <div className="flex items-center gap-2 border-b border-slate-200 px-3 dark:border-border">
        <Search className="h-4 w-4 shrink-0 text-slate-400" />
        <Command.Input
          placeholder="Search boards, jump to a section…"
          className="w-full bg-transparent py-3 text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-foreground"
        />
      </div>
      <Command.List className="max-h-80 overflow-auto p-1.5 scrollbar-thin">
        <Command.Empty className="px-3 py-6 text-center text-sm text-slate-400">
          No results.
        </Command.Empty>

        <Command.Group
          heading="Navigation"
          className="px-1 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-slate-400 [&_[cmdk-group-items]]:mt-1"
        >
          <PaletteItem
            icon={<LayoutGrid className="h-4 w-4" />}
            label="Boards"
            onSelect={() => run(() => navigate("/boards"))}
          />
          <PaletteItem
            icon={<MessagesSquare className="h-4 w-4" />}
            label="Chat"
            onSelect={() => run(() => navigate("/chat"))}
          />
        </Command.Group>

        {(boards.data?.length ?? 0) > 0 && (
          <Command.Group
            heading="Boards"
            className="px-1 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-slate-400 [&_[cmdk-group-items]]:mt-1"
          >
            {boards.data?.map((b) => (
              <PaletteItem
                key={b.id}
                icon={<LayoutGrid className="h-4 w-4" />}
                label={b.name}
                value={`board ${b.name} ${b.slug}`}
                onSelect={() => run(() => navigate(`/boards/${b.slug}`))}
              />
            ))}
          </Command.Group>
        )}
      </Command.List>
    </Command.Dialog>
  );
}

function PaletteItem({
  icon,
  label,
  value,
  onSelect,
}: {
  icon: React.ReactNode;
  label: string;
  value?: string;
  onSelect: () => void;
}) {
  return (
    <Command.Item
      value={value ?? label}
      onSelect={onSelect}
      className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-slate-700 transition-colors aria-selected:bg-slate-100 dark:text-foreground dark:aria-selected:bg-surface-2"
    >
      <span className="text-slate-400">{icon}</span>
      <span className="truncate">{label}</span>
    </Command.Item>
  );
}
