"use client";

import * as S from "@radix-ui/react-switch";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Switch({
  checked,
  onCheckedChange,
  label,
  description,
  disabled,
  className,
}: {
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <label className={cn("flex items-start justify-between gap-4", disabled && "opacity-60", className)}>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-ink">{label}</span>
        {description && <span className="mt-0.5 block text-[0.82rem] text-muted">{description}</span>}
      </span>
      <S.Root
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        className="relative mt-0.5 h-6 w-10 shrink-0 rounded-full bg-rule-strong transition-colors data-[state=checked]:bg-action"
      >
        <S.Thumb className="block size-5 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[18px]" />
      </S.Root>
    </label>
  );
}
