import { forwardRef, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      // White field, hairline border; light-blue border on focus (no glow).
      "flex min-h-[60px] w-full rounded border border-input bg-card px-3 py-2 text-sm transition-colors duration-100 placeholder:text-muted-foreground/50 hover:border-border-strong focus-visible:border-[#4C9AFF] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";
