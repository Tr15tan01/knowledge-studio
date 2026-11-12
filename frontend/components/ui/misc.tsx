"use client";

import { AlertTriangle, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber, percent } from "@/lib/format";
import { Button } from "./button";

type Tone = "neutral" | "action" | "ok" | "warn" | "danger" | "highlight";

const tones: Record<Tone, string> = {
  neutral: "bg-surface-2 text-ink-2",
  action: "bg-action-soft text-action",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  highlight: "bg-highlight-soft text-highlight-ink",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.76rem] font-medium leading-5", tones[tone], className)}>{children}</span>;
}

/** Horizontal usage meter. Colour shifts only when the user is near or at the limit. */
export function UsageBar({ used, limit, reserved = 0, warnAt = 80, label }: { used: number; limit: number; reserved?: number; warnAt?: number; label: string }) {
  const p = percent(used, limit);
  const pr = percent(used + reserved, limit);
  const tone = p >= 100 ? "bg-danger" : p >= warnAt ? "bg-warn" : "bg-action";
  return (
    <div
      className="relative h-2 w-full overflow-hidden rounded-full bg-surface-2"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={limit}
      aria-valuenow={used}
      aria-valuetext={`${formatNumber(used)} of ${formatNumber(limit)}`}
    >
      {reserved > 0 && <div className="absolute inset-y-0 left-0 bg-action/25" style={{ width: `${pr}%` }} />}
      <div className={cn("absolute inset-y-0 left-0 rounded-full transition-[width] duration-500", tone)} style={{ width: `${p}%` }} />
    </div>
  );
}

export function PageHeader({ title, description, actions, back }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; back?: ReactNode }) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        {back && <div className="mb-2">{back}</div>}
        <h1 className="text-[1.9rem] font-semibold leading-tight tracking-[-0.02em]">{title}</h1>
        {description && <p className="mt-1.5 max-w-2xl text-[0.97rem] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Section({ title, description, actions, children, className }: { title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-[var(--radius-panel)] border border-rule bg-surface", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-rule px-5 py-3.5">
          <div>
            {title && <h2 className="text-[1.02rem] font-semibold">{title}</h2>}
            {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      {icon && <div className="mb-4 rounded-full bg-surface-2 p-3.5 text-muted">{icon}</div>}
      <h3 className="text-lg font-semibold">{title}</h3>
      {children && <div className="mt-1.5 max-w-md text-[0.95rem] text-muted">{children}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const e = error instanceof ApiError ? error : null;
  const message = e?.message || (error instanceof Error ? error.message : "Something went wrong.");
  return (
    <div role="alert" className={cn("flex items-start gap-3 rounded-[var(--radius-panel)] border border-danger/25 bg-danger-soft text-danger", compact ? "px-3 py-2.5 text-sm" : "px-4 py-3.5")}>
      <AlertTriangle className="mt-0.5 size-4.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{message}</p>
        {e?.requestId && <p className="mt-0.5 text-xs opacity-75">Reference {e.requestId}</p>}
      </div>
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          <RefreshCw className="size-3.5" /> Retry
        </Button>
      )}
    </div>
  );
}

export function Notice({ tone = "neutral", children, className, icon }: { tone?: Tone; children: ReactNode; className?: string; icon?: ReactNode }) {
  return (
    <div className={cn("flex items-start gap-2.5 rounded-[10px] px-3.5 py-2.5 text-sm", tones[tone], className)}>
      {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <span className={cn("skeleton block h-4", className)} aria-hidden />;
}

export function LoadingRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3 py-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={cn("h-11", i % 2 ? "w-11/12" : "w-full")} />
      ))}
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return null;
  return (
    <nav className="mt-4 flex items-center justify-between gap-3 text-sm text-muted" aria-label="Pagination">
      <span>
        {formatNumber((page - 1) * pageSize + 1)}–{formatNumber(Math.min(total, page * pageSize))} of {formatNumber(total)}
      </span>
      <div className="flex items-center gap-1">
        <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          <ChevronLeft className="size-4" />
        </Button>
        <span className="px-2 tabular-nums">
          {page} / {pages}
        </span>
        <Button size="sm" variant="ghost" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page">
          <ChevronRight className="size-4" />
        </Button>
      </div>
    </nav>
  );
}

export function Stat({ label, value, hint, href }: { label: string; value: ReactNode; hint?: ReactNode; href?: string }) {
  const body = (
    <>
      <div className="text-sm text-muted">{label}</div>
      <div className="mt-1 text-[1.65rem] font-semibold tabular-nums tracking-[-0.02em]">{value}</div>
      {hint && <div className="mt-0.5 text-[0.8rem] text-faint">{hint}</div>}
    </>
  );
  return href ? (
    <a href={href} className="block rounded-lg px-1 py-1 transition-colors hover:bg-surface-2">
      {body}
    </a>
  ) : (
    <div className="px-1 py-1">{body}</div>
  );
}

export function Tabs<T extends string>({ value, onChange, items, className }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode; count?: number }[]; className?: string }) {
  return (
    <div role="tablist" className={cn("flex gap-1 overflow-x-auto border-b border-rule", className)}>
      {items.map((it) => (
        <button
          key={it.value}
          role="tab"
          aria-selected={value === it.value}
          onClick={() => onChange(it.value)}
          className={cn(
            "-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            value === it.value ? "border-action text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {it.label}
          {it.count !== undefined && <span className="ml-1.5 tabular-nums text-faint">{formatNumber(it.count)}</span>}
        </button>
      ))}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-rule-strong bg-surface-2 px-1.5 py-0.5 text-[0.72rem] font-medium text-muted">{children}</kbd>;
}

/** Compact metadata line: items separated by spacing rather than punctuation. */
export function Meta({ items, className }: { items: (ReactNode | null | false | undefined)[]; className?: string }) {
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.82rem] text-muted", className)}>
      {items.filter(Boolean).map((it, i) => (
        <span key={i}>{it}</span>
      ))}
    </span>
  );
}
