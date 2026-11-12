"use client";

import { useMemo, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Tabs } from "@/components/ui/misc";

type Day = { date: string; uploads: number; questions: number; reports: number };

const SERIES = {
  questions: { label: "Questions", color: "var(--action)" },
  uploads: { label: "Uploads", color: "var(--ink-2)" },
  reports: { label: "Reports", color: "var(--highlight)" },
} as const;

function shortDate(d: string) {
  const [, m, day] = d.split("-");
  return `${Number(day)}/${Number(m)}`;
}

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: { name: string; value: number; color: string }[]; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-rule bg-surface px-3 py-2 text-xs shadow-[var(--shadow-pop)]">
      <div className="mb-1 font-medium">{label && new Date(label).toLocaleDateString(undefined, { dateStyle: "medium" })}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2 text-ink-2">
          <span className="size-2 rounded-full" style={{ background: p.color }} />
          {p.name}: <span className="tabular-nums font-medium text-ink">{p.value}</span>
        </div>
      ))}
    </div>
  );
}

export function ActivityChart({ days }: { days: Day[] }) {
  const [view, setView] = useState<"activity" | "growth">("activity");
  const growth = useMemo(() => {
    const out: { date: string; files: number; reports: number }[] = [];
    let files = 0;
    let reports = 0;
    for (const d of days) {
      files += d.uploads;
      reports += d.reports;
      out.push({ date: d.date, files, reports });
    }
    return out;
  }, [days]);
  const empty = days.every((d) => !d.uploads && !d.questions && !d.reports);

  return (
    <div>
      <Tabs
        value={view}
        onChange={setView}
        items={[
          { value: "activity", label: "Daily activity" },
          { value: "growth", label: "Library growth" },
        ]}
      />
      <div className="relative mt-4 h-56" role="img" aria-label={view === "activity" ? "Daily questions, uploads and reports, last 30 days" : "Cumulative uploads and reports, last 30 days"}>
        {empty && <div className="absolute inset-0 z-10 grid place-items-center text-sm text-muted">No activity in the last 30 days yet.</div>}
        <ResponsiveContainer width="100%" height="100%">
          {view === "activity" ? (
            <BarChart data={days} margin={{ left: -18, right: 4, top: 4 }} barCategoryGap="22%">
              <CartesianGrid vertical={false} stroke="var(--rule)" />
              <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 11, fill: "var(--faint)" }} tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={24} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "var(--faint)" }} tickLine={false} axisLine={false} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--surface-2)" }} />
              {(Object.keys(SERIES) as (keyof typeof SERIES)[]).map((k) => (
                <Bar key={k} dataKey={k} name={SERIES[k].label} stackId="a" fill={SERIES[k].color} radius={k === "reports" ? [3, 3, 0, 0] : 0} />
              ))}
            </BarChart>
          ) : (
            <AreaChart data={growth} margin={{ left: -18, right: 4, top: 4 }}>
              <CartesianGrid vertical={false} stroke="var(--rule)" />
              <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 11, fill: "var(--faint)" }} tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={24} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "var(--faint)" }} tickLine={false} axisLine={false} />
              <Tooltip content={<ChartTooltip />} />
              <Area type="monotone" dataKey="files" name="Files added" stroke="var(--action)" fill="var(--action-soft)" strokeWidth={2} />
              <Area type="monotone" dataKey="reports" name="Reports" stroke="var(--warn)" fill="var(--highlight-soft)" strokeWidth={2} />
            </AreaChart>
          )}
        </ResponsiveContainer>
      </div>
      <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted">
        {view === "activity"
          ? Object.values(SERIES).map((s) => (
              <span key={s.label} className="inline-flex items-center gap-1.5">
                <span className="size-2.5 rounded-sm" style={{ background: s.color }} /> {s.label}
              </span>
            ))
          : [
              ["Files added", "var(--action)"],
              ["Reports", "var(--warn)"],
            ].map(([l, c]) => (
              <span key={l} className="inline-flex items-center gap-1.5">
                <span className="size-2.5 rounded-sm" style={{ background: c }} /> {l}
              </span>
            ))}
        <span className="ml-auto">Last 30 days, UTC</span>
      </div>
    </div>
  );
}
