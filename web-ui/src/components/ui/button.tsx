import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

// Classic Jira button (j-button): 32px tall, 3px radius, 14.5px label.
// primary = solid Jira blue (hover lightens); secondary = neutral #F4F5F7
// with *normal* weight; ghost ("btn-empty") = transparent, hover gray,
// pressed light-blue tint + link text.
const buttonVariants = cva(
  "inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded transition-colors duration-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-primary font-medium text-primary-foreground hover:bg-primary/85 active:bg-primary",
        destructive:
          "bg-destructive font-medium text-destructive-foreground hover:bg-destructive/90 active:bg-destructive/80",
        outline:
          "border border-border-strong bg-transparent font-normal text-foreground hover:bg-surface-1 active:bg-primary/10 active:text-primary",
        secondary:
          "bg-surface-1 font-normal text-foreground hover:bg-surface-3 active:bg-primary/10 active:text-primary",
        ghost:
          "font-normal text-foreground hover:bg-surface-1 active:bg-primary/10 active:text-primary",
        link: "font-normal text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-8 px-3 text-[14px]",
        sm: "h-7 px-2.5 text-xs",
        lg: "h-10 px-4 text-sm",
        icon: "h-8 w-8",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { buttonVariants };
