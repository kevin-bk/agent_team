import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      // White field, hairline border; light-blue border on focus (no glow).
      "flex h-8 w-full rounded border border-input bg-card px-3 py-1 text-sm transition-colors duration-100 placeholder:text-muted-foreground/50 hover:border-border-strong focus-visible:border-[#4C9AFF] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";
