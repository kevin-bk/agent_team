import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

// Atlassian Design System Lozenge: compact uppercase status label, 11px bold
// with subtle role-tinted background. Variants map to ADS status colors
// (inprogress=blue, success=green, removed=red, moved=yellow, new=purple).
const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] font-bold uppercase leading-4 tracking-[0.02em] transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary/15 text-primary",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-muted-foreground",
        success:
          "border-transparent bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
        destructive:
          "border-transparent bg-destructive/15 text-destructive",
        warning:
          "border-transparent bg-amber-400/20 text-amber-700 dark:text-amber-300",
        discovery:
          "border-transparent bg-discovery/15 text-discovery",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
