"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import Link from "next/link";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { EmptyState, ErrorState, LoadingRows, Notice, PageHeader, Section, Stat } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";
import { useReauth } from "@/features/auth/reauth";
import { hasRole, useUser } from "@/features/auth/session";
import { useUrlState } from "@/hooks/use-url-state";
import { ApiError, get, put } from "@/lib/api";
import { formatBytes, formatNumber, formatUsd } from "@/lib/format";
import type { AdminPlan, QuotaPolicy, UsageReport } from "@/types/admin";

const iso = (d: Date) => d.toISOString().slice(0, 10);

function UsageView() {
  const today = new Date();
  const [f, setF] = useUrlState({ from: iso(new Date(today.getTime() - 30 * 86_400_000)), to: iso(new Date(today.getTime() + 86_400_000)), user_id: "", plan_id: "", provider: "", model: "", operation: "" });
  const plans = useQuery({ queryKey: ["admin-plans"], queryFn: () => get<{ items: AdminPlan[] }>("/admin/plans") });
  const report = useQuery({
    queryKey: ["admin-usage", f],
    queryFn: () => get<UsageReport>("/admin/usage", { ...f, from: `${f.from}T00:00:00Z`, to: `${f.to}T00:00:00Z` }),
    placeholderData: (p) => p,
  });
  const r = report.data;
  const m = r?.usage_metrics || {};
  const totalCost = r?.providers.reduce((a, p) => a + p.estimated_cost_usd, 0) ?? 0;
  const tokensIn = r?.providers.reduce((a, p) => a + p.input_tokens, 0) ?? 0;
  const tokensOut = r?.providers.reduce((a, p) => a + p.output_tokens, 0) ?? 0;
  const failures = r?.providers.reduce((a, p) => a + p.errors, 0) ?? 0;
  const retries = r?.providers.reduce((a, p) => a + p.retries, 0) ?? 0;

  return (
    <>
      <PageHeader
        title="Usage & costs"
        description="Quota usage from usage events and AI usage from provider call logs. Costs are estimates from the model price table below."
        actions={
          <a href={`/api/v1/admin/usage/export.csv?from=${f.from}T00:00:00Z&to=${f.to}T00:00:00Z`} className="inline-flex h-10 items-center gap-2 rounded-[var(--radius-control)] border border-rule-strong bg-surface px-4 text-[0.94rem] font-medium hover:bg-surface-2">
            <Download className="size-4" /> Export CSV
          </a>
        }
      />
      <div className="mb-6 grid gap-3 sm:grid-cols-3 lg:grid-cols-7">
        <Field label="From">{(p) => <Input {...p} type="date" value={f.from} onChange={(e) => setF({ from: e.target.value })} />}</Field>
        <Field label="To">{(p) => <Input {...p} type="date" value={f.to} onChange={(e) => setF({ to: e.target.value })} />}</Field>
        <Field label="Plan">
          {(p) => (
            <Select {...p} value={f.plan_id} onChange={(e) => setF({ plan_id: e.target.value })}>
              <option value="">All</option>
              {plans.data?.items.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="User ID">{(p) => <Input {...p} value={f.user_id} onChange={(e) => setF({ user_id: e.target.value.trim() })} />}</Field>
        <Field label="Provider">{(p) => <Input {...p} value={f.provider} onChange={(e) => setF({ provider: e.target.value.trim() })} placeholder="gemini" />}</Field>
        <Field label="Model">{(p) => <Input {...p} value={f.model} onChange={(e) => setF({ model: e.target.value.trim() })} />}</Field>
        <Field label="Operation">{(p) => <Input {...p} value={f.operation} onChange={(e) => setF({ operation: e.target.value.trim() })} placeholder="standard_question" />}</Field>
      </div>
      {report.isError && <ErrorState error={report.error} onRetry={() => report.refetch()} />}
      {!r ? (
        <LoadingRows rows={6} />
      ) : (
        <div className="space-y-6">
          <Section>
            <div className="grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-5">
              <Stat label="Uploads" value={formatNumber(m.uploads?.total ?? 0)} />
              <Stat label="Storage added" value={formatBytes(m.storage_bytes?.total ?? 0)} />
              <Stat label="AI questions" value={formatNumber(m.questions?.total ?? 0)} />
              <Stat label="Research reports" value={formatNumber(m.research_reports?.total ?? 0)} />
              <Stat label="Quota denials" value={formatNumber(r.quota_denials)} />
              <Stat label="Estimated cost" value={formatUsd(totalCost)} hint="Estimate" />
              <Stat label="Input tokens" value={formatNumber(tokensIn)} />
              <Stat label="Output tokens" value={formatNumber(tokensOut)} />
              <Stat label="Embedding requests" value={formatNumber(r.embedding_requests)} />
              <Stat label="Provider failures" value={formatNumber(failures)} hint={`${formatNumber(retries)} retries`} />
            </div>
          </Section>
          <Section title="By provider, model and operation">
            {r.providers.length === 0 ? (
              <EmptyState title="No provider calls in this range" />
            ) : (
              <div className="-mx-5 -my-4 overflow-x-auto">
                <table className="w-full min-w-[860px] text-sm">
                  <thead className="border-b border-rule text-left text-[0.8rem] text-muted">
                    <tr>
                      {["Provider", "Model", "Operation", "Calls", "Errors", "Retries", "Tokens in", "Tokens out", "Avg latency", "Est. cost"].map((h, i) => (
                        <th key={h} className={`${i === 0 ? "px-5" : ""} ${i > 2 ? "text-right" : ""} py-2.5 font-medium ${i === 9 ? "pr-5" : ""}`}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-rule tabular-nums">
                    {r.providers.map((p, i) => (
                      <tr key={i}>
                        <td className="px-5 py-2">{p.provider}</td>
                        <td className="py-2">{p.model}</td>
                        <td className="py-2">{p.operation}</td>
                        <td className="py-2 text-right">{formatNumber(p.calls)}</td>
                        <td className="py-2 text-right">{formatNumber(p.errors)}</td>
                        <td className="py-2 text-right">{formatNumber(p.retries)}</td>
                        <td className="py-2 text-right">{formatNumber(p.input_tokens)}</td>
                        <td className="py-2 text-right">{formatNumber(p.output_tokens)}</td>
                        <td className="py-2 text-right">{formatNumber(Math.round(p.avg_latency_ms))} ms</td>
                        <td className="py-2 pr-5 text-right">{formatUsd(p.estimated_cost_usd, 4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
          {r.top_accounts.length > 0 && (
            <Section title="Highest-cost accounts" description="Account IDs only, to minimise personal data. Open one to see the user.">
              <ul className="-my-1 divide-y divide-rule text-sm">
                {r.top_accounts.map((a) => (
                  <li key={a.account_id ?? "platform"} className="flex justify-between gap-3 py-2 tabular-nums">
                    {a.account_id ? (
                      <Link href={`/admin/users/${a.account_id}`} className="text-action hover:underline">
                        {a.account_id}
                      </Link>
                    ) : (
                      <span className="text-muted">Platform (no account)</span>
                    )}
                    <span>
                      {formatNumber(a.calls)} calls, {formatUsd(a.estimated_cost_usd, 4)}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          <div className="grid gap-6 xl:grid-cols-2">
            <ModelPrices />
            <QuotaPolicyEditor />
          </div>
        </div>
      )}
    </>
  );
}

type PriceTable = { prices: Record<string, { input: number; output: number }>; unit: string; note: string };

function ModelPrices() {
  const q = useQuery({ queryKey: ["admin-model-prices"], queryFn: () => get<PriceTable>("/admin/model-prices"), staleTime: Infinity });
  if (!q.data) return <Section title="Model price table">{q.isError ? <ErrorState error={q.error} /> : <LoadingRows rows={3} />}</Section>;
  return <ModelPricesEditor key={q.dataUpdatedAt} data={q.data} />;
}

function ModelPricesEditor({ data }: { data: PriceTable }) {
  const qc = useQueryClient();
  const superAdmin = hasRole(useUser(), "SUPER_ADMIN");
  const q = { data };
  const [rows, setRows] = useState<[string, number, number][]>(() => Object.entries(data.prices).map(([k, v]) => [k, v.input, v.output]));
  const [reason, setReason] = useState("");
  async function save() {
    try {
      await put("/admin/model-prices", { prices: Object.fromEntries(rows.filter((r) => r[0].trim()).map(([k, i, o]) => [k.trim(), { input: i, output: o }])), reason });
      toast("Price table saved.");
      setReason("");
      qc.invalidateQueries({ queryKey: ["admin-model-prices"] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't save.");
    }
  }
  return (
    <Section title="Model price table" description={q.data ? `${q.data.unit}. ${q.data.note} Check your provider's current pricing.` : undefined}>
      <div className="space-y-2">
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-[1fr_96px_96px] gap-2">
            <Input value={r[0]} disabled={!superAdmin} aria-label="Model" onChange={(e) => setRows(rows.map((x, j) => (j === i ? [e.target.value, x[1], x[2]] : x)))} />
            <Input type="number" step="0.01" min={0} value={r[1]} disabled={!superAdmin} aria-label="Input price" onChange={(e) => setRows(rows.map((x, j) => (j === i ? [x[0], Number(e.target.value), x[2]] : x)))} />
            <Input type="number" step="0.01" min={0} value={r[2]} disabled={!superAdmin} aria-label="Output price" onChange={(e) => setRows(rows.map((x, j) => (j === i ? [x[0], x[1], Number(e.target.value)] : x)))} />
          </div>
        ))}
        <div className="grid grid-cols-[1fr_96px_96px] gap-2 text-[0.76rem] text-faint">
          <span>Model name (or default / embedding)</span>
          <span>Input</span>
          <span>Output</span>
        </div>
        {superAdmin && (
          <div className="flex flex-wrap items-center gap-2 pt-2">
            <Button size="sm" variant="ghost" onClick={() => setRows([...rows, ["", 0, 0]])}>
              Add model
            </Button>
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for change" className="h-8 min-w-40 flex-1" aria-label="Reason" />
            <Button size="sm" onClick={save} disabled={reason.trim().length < 3}>
              Save prices
            </Button>
          </div>
        )}
      </div>
    </Section>
  );
}

function QuotaPolicyEditor() {
  const q = useQuery({ queryKey: ["admin-quota-policy"], queryFn: () => get<QuotaPolicy>("/admin/quota-policy"), staleTime: Infinity });
  if (!q.data) return <Section title="Quota policy">{q.isError ? <ErrorState error={q.error} /> : <LoadingRows rows={3} />}</Section>;
  return <QuotaPolicyForm key={q.dataUpdatedAt} initial={q.data} />;
}

function QuotaPolicyForm({ initial }: { initial: QuotaPolicy }) {
  const qc = useQueryClient();
  const reauth = useReauth();
  const superAdmin = hasRole(useUser(), "SUPER_ADMIN");
  const [p, setP] = useState<QuotaPolicy>(initial);
  const [reason, setReason] = useState("");
  async function save() {
    try {
      await reauth(() => put("/admin/quota-policy", { ...p, reason }));
      toast("Quota policy saved.");
      setReason("");
      qc.invalidateQueries({ queryKey: ["admin-quota-policy"] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't save.");
    }
  }
  return (
    <Section title="Quota policy" description="Rules that apply to every plan.">
      <fieldset disabled={!superAdmin} className="space-y-5">
        <Switch
          checked={p.upload_quota_restore_on_delete}
          onCheckedChange={(v) => setP({ ...p, upload_quota_restore_on_delete: v })}
          label="Deleting a file restores an upload"
          description="Off by default: uploads count accepted new files. Storage is always released on confirmed deletion."
        />
        <Field label="Storage warning thresholds (%)" hint="Comma-separated, for example 80, 90, 100.">
          {(f) => (
            <Input
              {...f}
              value={p.storage_warning_thresholds.join(", ")}
              onChange={(e) =>
                setP({
                  ...p,
                  storage_warning_thresholds: e.target.value
                    .split(",")
                    .map((x) => Number(x.trim()))
                    .filter((n) => Number.isFinite(n) && n > 0),
                })
              }
            />
          )}
        </Field>
        <div>
          <div className="mb-2 text-sm font-medium">Questions counted per operation</div>
          <div className="grid gap-3 sm:grid-cols-2">
            {Object.entries(p.operation_credits).map(([k, v]) => (
              <label key={k} className="flex items-center justify-between gap-2 text-sm">
                <span className="text-ink-2">{k.replace(/_/g, " ")}</span>
                <Input type="number" min={0} max={50} value={v} onChange={(e) => setP({ ...p, operation_credits: { ...p.operation_credits, [k]: Number(e.target.value) } })} className="w-20" />
              </label>
            ))}
          </div>
          <p className="mt-2 text-[0.8rem] text-muted">Deep research uses the separate report allowance. Keep values simple: users see questions, not credits.</p>
        </div>
        <Notice tone="neutral">
          Fixed rules: replacing a file counts as a new upload; failed operations release their reservation; usage over a lowered limit blocks new usage without deleting anything.
        </Notice>
        {superAdmin && (
          <div className="flex flex-wrap gap-2">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for change" className="min-w-40 flex-1" aria-label="Reason" />
            <Button onClick={save} disabled={reason.trim().length < 3}>
              Save policy
            </Button>
          </div>
        )}
      </fieldset>
    </Section>
  );
}

export default function AdminUsagePage() {
  return (
    <Suspense fallback={<LoadingRows rows={6} />}>
      <UsageView />
    </Suspense>
  );
}
