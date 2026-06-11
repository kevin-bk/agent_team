import { Check, ChevronDown, Cpu } from "@/components/icons";
import { useProfiles } from "@/api/hooks";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export function ProfilePicker({
  value,
  onChange,
}: {
  value: string | undefined;
  onChange: (profile: string) => void;
}) {
  const { data: profiles, isLoading } = useProfiles();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="flex w-full items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm transition-colors hover:bg-accent">
          <Cpu className="h-4 w-4 text-primary" />
          <span className="min-w-0 flex-1 truncate text-left font-medium">
            {value ?? (isLoading ? "Loading…" : "Select profile")}
          </span>
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[15rem]">
        {profiles?.map((p) => (
          <DropdownMenuItem
            key={p.name}
            onSelect={() => onChange(p.name)}
            className="flex items-center gap-2"
          >
            <Check
              className={cn(
                "h-4 w-4",
                p.name === value ? "opacity-100" : "opacity-0",
              )}
            />
            <span className="flex-1 truncate">{p.name}</span>
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                p.status === "running" ? "bg-emerald-400" : "bg-amber-400",
              )}
              title={p.status}
            />
          </DropdownMenuItem>
        ))}
        {profiles?.length === 0 && (
          <div className="px-2 py-1.5 text-sm text-muted-foreground">
            No profiles found.
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
