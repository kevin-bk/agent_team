import { Check, ChevronDown } from "@/components/icons";
import { useMemo, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./dropdown-menu";

export interface SelectMenuOption {
  value: string;
  label: string;
  /** Optional leading visual (priority arrow, status dot, avatar…). */
  icon?: ReactNode;
  /** Optional secondary line under the label. */
  description?: string;
}

/**
 * Jira-style single select: an input-look trigger that opens a proper menu
 * with icons, hover states and a check on the selected row — replaces the
 * bare native `<select>` in forms.
 */
export function SelectMenu({
  value,
  onChange,
  options,
  placeholder = "Select…",
  disabled,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  options: SelectMenuOption[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const selected = useMemo(
    () => options.find((o) => o.value === value),
    [options, value],
  );

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <button
          type="button"
          className={cn(
            "flex h-8 w-full items-center gap-2 rounded border border-input bg-card px-3 text-left text-sm text-foreground transition-colors duration-100 hover:border-border-strong focus-visible:border-[#4C9AFF] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 data-[state=open]:border-[#4C9AFF]",
            className,
          )}
        >
          {selected?.icon && (
            <span className="flex shrink-0 items-center">{selected.icon}</span>
          )}
          <span
            className={cn(
              "min-w-0 flex-1 truncate",
              !selected && "text-muted-foreground/50",
            )}
          >
            {selected?.label ?? placeholder}
          </span>
          <ChevronDown
            className={cn(
              "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150",
              open && "rotate-180",
            )}
          />
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="start"
        className="max-h-72 w-[var(--radix-dropdown-menu-trigger-width)] overflow-y-auto scrollbar-thin"
      >
        {options.map((o) => {
          const isSelected = o.value === value;
          return (
            <DropdownMenuItem
              key={o.value}
              onSelect={() => onChange(o.value)}
              className={cn(
                "gap-2 py-1.5",
                isSelected && "bg-primary/10 text-primary focus:bg-primary/10 focus:text-primary",
              )}
            >
              {o.icon && (
                <span className="flex shrink-0 items-center">{o.icon}</span>
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">{o.label}</span>
                {o.description && (
                  <span className="block truncate text-[12px] text-muted-foreground">
                    {o.description}
                  </span>
                )}
              </span>
              {isSelected && <Check className="h-4 w-4 shrink-0 text-primary" />}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
