import Link from "next/link";
import { forwardRef, type ButtonHTMLAttributes, type ComponentProps } from "react";
import { cn } from "@/lib/cn";
import { Spinner } from "./spinner";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "link";
type Size = "sm" | "md" | "lg";

const base =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-medium transition-colors select-none disabled:opacity-55 disabled:pointer-events-none";

const variants: Record<Variant, string> = {
  primary: "bg-action text-action-ink hover:bg-action-hover shadow-[inset_0_-1px_0_rgba(0,0,0,0.18)]",
  secondary: "bg-surface text-ink border border-rule-strong hover:border-ink-2/50 hover:bg-surface-2",
  ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
  danger: "bg-danger text-white hover:opacity-90",
  link: "text-action underline-offset-4 hover:underline px-0! h-auto!",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-sm rounded-[var(--radius-control)]",
  md: "h-10 px-4 text-[0.94rem] rounded-[var(--radius-control)]",
  lg: "h-12 px-6 text-base rounded-[9px]",
};

export function buttonClass(variant: Variant = "primary", size: Size = "md", className?: string) {
  return cn(base, variants[variant], sizes[size], className);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", loading, className, children, disabled, type = "button", ...props },
  ref,
) {
  return (
    <button ref={ref} type={type} className={buttonClass(variant, size, className)} disabled={disabled || loading} aria-busy={loading || undefined} {...props}>
      {loading && <Spinner className="size-4" />}
      {children}
    </button>
  );
});

export function ButtonLink({
  variant = "primary",
  size = "md",
  className,
  ...props
}: ComponentProps<typeof Link> & { variant?: Variant; size?: Size }) {
  return <Link className={buttonClass(variant, size, className)} {...props} />;
}

export function IconButton({ label, className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn("inline-flex size-9 items-center justify-center rounded-[var(--radius-control)] text-muted hover:bg-surface-2 hover:text-ink transition-colors", className)}
      {...props}
    />
  );
}
