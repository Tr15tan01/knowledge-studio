"use client";

import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Button } from "./button";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = "md",
  side,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  /** Render as a side sheet (used for source previews and graph details). */
  side?: "right";
}) {
  const w = { sm: "max-w-md", md: "max-w-lg", lg: "max-w-2xl", xl: "max-w-4xl" }[size];
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-[rgba(18,21,42,0.42)] backdrop-blur-[2px] data-[state=open]:animate-[fade_150ms_ease-out]" />
        <D.Content
          className={cn(
            "fixed z-50 flex flex-col bg-surface text-ink shadow-[var(--shadow-pop)] focus:outline-none",
            side === "right"
              ? "inset-y-0 right-0 w-full max-w-xl border-l border-rule data-[state=open]:animate-[slidein_200ms_cubic-bezier(0.2,0.8,0.2,1)]"
              : cn("left-1/2 top-1/2 max-h-[88vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-panel)] border border-rule", w),
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-rule px-5 py-4">
            <div className="min-w-0">
              <D.Title className="text-lg font-semibold leading-snug">{title}</D.Title>
              {description ? <D.Description className="mt-1 text-sm text-muted">{description}</D.Description> : <D.Description className="sr-only">Dialog</D.Description>}
            </div>
            <D.Close className="-mr-1.5 rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Close">
              <X className="size-4.5" />
            </D.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-rule px-5 py-3.5">{footer}</div>}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

/** Confirmation for destructive or significant actions. Optionally requires a reason. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  danger,
  requireReason,
  reasonLabel = "Reason",
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  requireReason?: boolean;
  reasonLabel?: string;
  onConfirm: (reason: string) => Promise<unknown> | unknown;
  children?: ReactNode;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const valid = !requireReason || reason.trim().length >= 3;
  async function go() {
    setBusy(true);
    setErr(null);
    try {
      await onConfirm(reason.trim());
      setReason("");
      onOpenChange(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!busy) onOpenChange(v);
      }}
      title={title}
      description={description}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button variant={danger ? "danger" : "primary"} onClick={go} loading={busy} disabled={!valid}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
      {requireReason && (
        <label className="mt-1 block space-y-1.5">
          <span className="text-sm font-medium">{reasonLabel}</span>
          <textarea
            className="w-full rounded-[var(--radius-control)] border border-rule-strong bg-surface px-3 py-2 text-sm focus:border-action focus:outline-none focus:ring-2 focus:ring-action/25"
            rows={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Recorded in the audit log"
          />
        </label>
      )}
      {err && (
        <p role="alert" className="mt-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {err}
        </p>
      )}
    </Dialog>
  );
}
