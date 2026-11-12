"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Select } from "@/components/ui/field";
import { Badge, ErrorState, LoadingRows, PageHeader, Section, Stat } from "@/components/ui/misc";
import { get } from "@/lib/api";
import { formatBytes, formatMoneyMinor, formatNumber, formatUsd } from "@/lib/format";
import type { Overview } from "@/types/admin";

const axis = { tick: { fontSize: 11, fill: "var(--faint)" }, tickLine: false, axisLine: false } as const;

export default function AdminOverviewPage() {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["admin-overview", days], queryFn: () => get<Overview>("/admin/overview", { days }), placeholderData: (p) => p });
  const o = q.data;
  return (
    <>
      <PageHeader
        title="Overview"
        description="Live figures from the database. Costs and revenue are estimates; Paddle and your AI provider's billing are authoritative."
        actions={
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-auto" aria-label="Period">
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
            <option value={365}>Last 12 months</option>
          </Select>
        }
      />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {!o ? (
        <LoadingRows rows={8} />
      ) : (
        <div className="space-y-6">
          <Section>
            <div className="grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-4">
              <Stat label="Users" value={formatNumber(o.users.total)} hint={`${formatNumber(o.users.new)} new, ${formatNumber(o.users.active)} active`} href="/admin/users" />
              <Stat label="Paid subscriptions" value={formatNumber(o.subscriptions.paid_active)} />
              <Stat
                label="MRR (estimate)"
                value={Object.entries(o.mrr.by_currency_minor).map(([c, v]) => formatMoneyMinor(v, c)).join(" + ") || formatMoneyMinor(0)}
                hint={`${o.mrr.subscriptions_counted} subscriptions counted`}
              />
              <Stat label="Provider cost (estimate)" value={formatUsd(o.provider.estimated_cost_usd)} hint={`${formatNumber(o.provider.calls)} calls, ${formatNumber(o.provider.errors)} errors`} href="/admin/usage" />
              <Stat label="Uploads" value={formatNumber(o.usage.uploads)} />
              <Stat label="Stored" value={formatBytes(o.storage.bytes)} hint={`${formatNumber(o.storage.files)} files`} />
              <Stat label="AI questions" value={formatNumber(o.usage.questions)} hint={`${formatNumber(o.usage.quota_denials)} quota denials`} />
              <Stat label="Research reports" value={formatNumber(o.usage.research_reports)} />
              <Stat label="Failed jobs" value={formatNumber(o.failed_jobs)} href="/admin/jobs?status=failed" />
              <Stat label="Webhook signature rejections" value={formatNumber(o.billing_events.signature_rejections)} href="/admin/audit?tab=billing" />
              <Stat label="Research runs" value={formatNumber(Object.values(o.research_jobs).reduce((a, b) => a + b, 0))} hint={Object.entries(o.research_jobs).map(([k, v]) => `${v} ${k}`).join(", ") || "none"} />
              <Stat label="Tokens in / out" value={`${formatNumber(Math.round(o.provider.input_tokens / 1000))}k`} hint={`${formatNumber(Math.round(o.provider.output_tokens / 1000))}k out`} />
            </div>
          </Section>

          <div className="grid gap-6 lg:grid-cols-2">
            <Section title="Activity">
              <div className="h-60" role="img" aria-label="Daily registrations, uploads, questions and research runs">
                <ResponsiveContainer>
                  <BarChart data={o.timeseries} margin={{ left: -18, right: 4 }}>
                    <CartesianGrid vertical={false} stroke="var(--rule)" />
                    <XAxis dataKey="date" {...axis} minTickGap={28} tickFormatter={(d: string) => d.slice(5)} />
                    <YAxis allowDecimals={false} {...axis} />
                    <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--rule)", borderRadius: 8, fontSize: 12 }} />
                    <Bar dataKey="questions" name="Questions" stackId="a" fill="var(--action)" />
                    <Bar dataKey="uploads" name="Uploads" stackId="a" fill="var(--ink-2)" />
                    <Bar dataKey="research" name="Research" stackId="a" fill="var(--highlight)" />
                    <Bar dataKey="registrations" name="Registrations" stackId="a" fill="var(--ok)" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Section>
            <Section title="Estimated provider cost per day" description="USD, from token counts and the configured model price table.">
              <div className="h-60" role="img" aria-label="Estimated daily provider cost">
                <ResponsiveContainer>
                  <LineChart data={o.timeseries} margin={{ left: -6, right: 4 }}>
                    <CartesianGrid vertical={false} stroke="var(--rule)" />
                    <XAxis dataKey="date" {...axis} minTickGap={28} tickFormatter={(d: string) => d.slice(5)} />
                    <YAxis {...axis} tickFormatter={(v: number) => `$${v}`} />
                    <Tooltip formatter={(v) => formatUsd(Number(v), 4)} contentStyle={{ background: "var(--surface)", border: "1px solid var(--rule)", borderRadius: 8, fontSize: 12 }} />
                    <Line type="monotone" dataKey="estimated_cost_usd" name="Estimated cost" stroke="var(--action)" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Section>
          </div>

          <Section title="Plans" description="Subscriptions by plan and status.">
            <table className="w-full text-sm">
              <thead className="text-left text-muted">
                <tr>
                  <th className="py-1.5 font-medium">Plan</th>
                  <th className="py-1.5 font-medium">Source and status</th>
                  <th className="py-1.5 text-right font-medium">Accounts</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {Object.entries(o.subscriptions.by_plan).flatMap(([plan, rows]) =>
                  Object.entries(rows).map(([k, n]) => (
                    <tr key={plan + k}>
                      <td className="py-2 font-medium">{plan}</td>
                      <td className="py-2">
                        <Badge>{k.replace(":", ", ")}</Badge>
                      </td>
                      <td className="py-2 text-right tabular-nums">{formatNumber(n)}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
            <p className="mt-3 text-[0.8rem] text-muted">{o.mrr.basis}</p>
          </Section>
        </div>
      )}
    </>
  );
}
