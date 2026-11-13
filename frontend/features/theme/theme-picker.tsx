"use client";

import { Check } from "lucide-react";
import { THEMES, useTheme, type ThemeId, type ThemePref } from "@/features/theme/theme";
import { cn } from "@/lib/cn";

/** A miniature page drawn in a theme's own colours (`data-theme` re-themes just this element). */
function Swatch({ id, className }: { id: ThemeId; className?: string }) {
  return (
    <span data-theme={id} className={cn("flex h-16 flex-col justify-between rounded-md border border-rule bg-paper p-2", className)}>
      <span className="h-1.5 w-9 rounded-full bg-ink" />
      <span className="flex items-center justify-between rounded bg-surface px-1.5 py-1.5 shadow-sm">
        <span className="h-1 w-7 rounded-full bg-muted" />
        <span className="h-2.5 w-6 rounded-full bg-action" />
      </span>
    </span>
  );
}

export function ThemePicker({ onPick }: { onPick?: (t: ThemePref) => void }) {
  const { theme, setTheme } = useTheme();
  const options: { id: ThemePref; label: string }[] = [{ id: "system", label: "System" }, ...THEMES];
  return (
    <div className="grid max-w-3xl grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5" role="radiogroup" aria-label="Theme">
      {options.map((o) => {
        const active = theme === o.id;
        return (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => {
              setTheme(o.id);
              onPick?.(o.id);
            }}
            className={cn("rounded-[10px] border bg-surface p-1.5 text-left transition-colors", active ? "border-action ring-2 ring-action/25" : "border-rule hover:border-rule-strong")}
          >
            {o.id === "system" ? (
              <span className="relative block h-16">
                <Swatch id="blue" className="absolute inset-0" />
                <Swatch id="midnight" className="absolute inset-0 [clip-path:polygon(100%_0,100%_100%,0_100%)]" />
              </span>
            ) : (
              <Swatch id={o.id} />
            )}
            <span className="mt-1.5 flex items-center justify-between px-1 text-sm">
              <span className={cn(active && "font-medium")}>{o.label}</span>
              {active && <Check className="size-4 text-action" aria-hidden />}
            </span>
          </button>
        );
      })}
    </div>
  );
}
