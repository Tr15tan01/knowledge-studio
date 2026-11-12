"use client";

import { Check, Minus } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { formatMoneyMinor, formatNumber } from "@/lib/format";
import type { PublicPlan } from "@/types/api";

export type Interval = "month" | "year";

export function IntervalToggle({ value, onChange }: { value: Interval; onChange: (v: Interval) => void }) {
  return (
    <div role="radiogroup" aria-label="Billing interval" className="inline-flex rounded-full border border-rule-strong bg-surface p-1 text-sm">
      {(["month", "year"] as const).map((v) => (
        <button
          key={v}
          role="radio"
          aria-checked={value === v}
          onClick={() => onChange(v)}
          className={cn("rounded-full px-4 py-1.5 font-medium transition-colors", value === v ? "bg-ink text-paper" : "text-ink-2 hover:text-ink")}
        >
          {v === "month" ? "Monthly" : "Yearly"}
        </button>
      ))}
    </div>
  );
}

export function planPrice(p: PublicPlan, interval: Interval): { main: string; sub: string; saving: string | null } {
  const m = p.monthly_price_minor ?? 0;
  const y = p.annual_price_minor;
  if (!m && !y) return { main: formatMoneyMinor(0, p.currency), sub: "forever", saving: null };
  if (interval === "year" && y) {
    const saving = m ? Math.round((1 - y / (m * 12)) * 100) : 0;
    return { main: formatMoneyMinor(y, p.currency), sub: "per year", saving: saving > 0 ? `Save ${saving}% vs monthly` : null };
  }
  return { main: formatMoneyMinor(m, p.currency), sub: "per month", saving: null };
}

const LIMIT_ROWS: { label: string; get: (p: PublicPlan) => ReactNode; hint?: string }[] = [
  { label: "Uploads per month", get: (p) => formatNumber(p.limits.uploads_per_period), hint: "Accepted new files. Deleting a file doesn't restore an upload." },
  { label: "Storage", get: (p) => p.limits.storage_display, hint: "Original uploaded files, in binary units (1 GiB = 1,073,741,824 bytes)." },
  { label: "Largest file", get: (p) => p.limits.max_file_size_display },
  { label: "AI questions per month", get: (p) => formatNumber(p.limits.questions_per_period) },
  { label: "Research reports per month", get: (p) => formatNumber(p.limits.research_reports_per_period) },
  { label: "Research projects", get: (p) => formatNumber(p.limits.saved_research_projects) },
  { label: "Concurrent research runs", get: (p) => formatNumber(p.limits.concurrent_research_jobs) },
];

/**
 * Plan comparison driven entirely by the server's plan catalog. Prices are display prices;
 * the amount actually charged is defined by the Paddle price mapped to each plan.
 */
export function PlanTable({
  plans,
  interval,
  currentSlug,
  renderAction,
}: {
  plans: PublicPlan[];
  interval: Interval;
  currentSlug?: string;
  renderAction: (p: PublicPlan) => ReactNode;
}) {
  const features = plans[0]?.features.map((f) => ({ key: f.key, label: f.label })) ?? [];
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] border-separate border-spacing-0 text-sm">
        <thead>
          <tr>
            <th className="w-[34%]" />
            {plans.map((p) => {
              const price = planPrice(p, interval);
              const current = p.slug === currentSlug;
              return (
                <th key={p.id} scope="col" className={cn("px-4 pb-5 pt-4 text-left align-top font-normal", current && "rounded-t-[14px] bg-action-soft/60")}>
                  <div className="flex items-center gap-2">
                    <span className="text-lg font-semibold">{p.name}</span>
                    {current && <span className="rounded-full bg-action px-2 py-0.5 text-[0.72rem] font-medium text-action-ink">Current</span>}
                  </div>
                  <p className="mt-1 min-h-10 text-[0.85rem] text-muted">{p.tagline || p.description}</p>
                  <div className="mt-3 flex items-baseline gap-1.5">
                    <span className="text-[2rem] font-semibold tabular-nums tracking-[-0.02em]">{price.main}</span>
                    <span className="text-muted">{price.sub}</span>
                  </div>
                  <p className="h-5 text-[0.8rem] text-ok">{price.saving}</p>
                  <div className="mt-3">{renderAction(p)}</div>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {LIMIT_ROWS.map((row) => (
            <tr key={row.label}>
              <th scope="row" className="border-t border-rule py-2.5 pr-4 text-left font-normal text-ink-2">
                {row.label}
                {row.hint && <span className="block text-[0.76rem] text-faint">{row.hint}</span>}
              </th>
              {plans.map((p) => (
                <td key={p.id} className={cn("border-t border-rule px-4 py-2.5 font-medium tabular-nums", p.slug === currentSlug && "bg-action-soft/60")}>
                  {row.get(p)}
                </td>
              ))}
            </tr>
          ))}
          {features.map((f, i) => (
            <tr key={f.key}>
              <th scope="row" className="border-t border-rule py-2.5 pr-4 text-left font-normal text-ink-2">
                {f.label}
              </th>
              {plans.map((p) => {
                const on = p.features.find((x) => x.key === f.key)?.enabled;
                return (
                  <td
                    key={p.id}
                    className={cn("border-t border-rule px-4 py-2.5", p.slug === currentSlug && "bg-action-soft/60", i === features.length - 1 && p.slug === currentSlug && "rounded-b-[14px]")}
                  >
                    {on ? <Check className="size-4 text-ok" aria-label="Included" /> : <Minus className="size-4 text-faint" aria-label="Not included" />}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
