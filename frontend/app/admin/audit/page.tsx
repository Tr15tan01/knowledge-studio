"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense, useEffect, useState } from "react";
import { Input, Select } from "@/components/ui/field";
import { Badge, EmptyState, ErrorState, LoadingRows, Notice, PageHeader, Pagination, Tabs } from "@/components/ui/misc";
import { useDebounced, useUrlState } from "@/hooks/use-url-state";
import { get } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AuditItem, BillingEvent } from "@/types/admin";
import type { Paged } from "@/types/api";

function AuditView() {
  const [f, setF] = useUrlState({ tab: "audit", action: "", actor_id: "", target_id: "", status: "", page: "1" });
  const page = Number(f.page) || 1;
  const [action, setAction] = useState(f.action);
  const da = useDebounced(action);
  useEffect(() => {
    if (da !== f.action) setF({ action: da, page: "1" });
  }, [da]); // eslint-disable-line react-hooks/exhaustive-deps
  const audit = useQuery({
    queryKey: ["admin-audit", f],
    queryFn: () => get<Paged<AuditItem> & { immutable: boolean }>("/admin/audit", { action: f.action, actor_id: f.actor_id, target_id: f.target_id, page, page_size: 50 }),
    enabled: f.tab === "audit",
    placeholderData: (p) => p,
  });
  const events = useQuery({
    queryKey: ["admin-billing-events", f.status, page],
    queryFn: () => get<Paged<BillingEvent>>("/admin/billing/events", { status: f.status, page, page_size: 50 }),
    enabled: f.tab === "billing",
    placeholderData: (p) => p,
  });

  return (
    <>
      <PageHeader title="Audit & billing events" />
      <Tabs
        className="mb-5"
        value={f.tab}
        onChange={(v) => setF({ tab: v, page: "1" })}
        items={[
          { value: "audit", label: "Audit log" },
          { value: "billing", label: "Paddle webhook events" },
        ]}
      />
      {f.tab === "audit" ? (
        <>
          <Notice tone="neutral" className="mb-4">
            The audit log is append-only. No one, including super administrators, can edit or delete entries from the application.
          </Notice>
          <div className="mb-4 flex flex-wrap gap-2">
            <Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="Action prefix, e.g. admin. or billing." className="w-64" aria-label="Action" />
            <Input value={f.actor_id} onChange={(e) => setF({ actor_id: e.target.value.trim(), page: "1" })} placeholder="Actor user ID" className="w-56" aria-label="Actor" />
            <Input value={f.target_id} onChange={(e) => setF({ target_id: e.target.value.trim(), page: "1" })} placeholder="Target ID" className="w-56" aria-label="Target" />
          </div>
          {audit.isError ? (
            <ErrorState error={audit.error} />
          ) : !audit.data ? (
            <LoadingRows rows={8} />
          ) : !audit.data.items.length ? (
            <EmptyState title="No matching events" />
          ) : (
            <div className="overflow-x-auto rounded-[var(--radius-panel)] border border-rule bg-surface">
              <table className="w-full min-w-[860px] text-sm">
                <thead className="border-b border-rule text-left text-[0.8rem] text-muted">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">When</th>
                    <th className="py-2.5 font-medium">Action</th>
                    <th className="py-2.5 font-medium">Actor</th>
                    <th className="py-2.5 font-medium">Target</th>
                    <th className="py-2.5 pr-4 font-medium">Reason and details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-rule align-top">
                  {audit.data.items.map((a) => (
                    <tr key={a.id}>
                      <td className="whitespace-nowrap px-4 py-2 tabular-nums text-muted">{formatDateTime(a.created_at)}</td>
                      <td className="py-2 font-medium">{a.action}</td>
                      <td className="py-2">
                        <Badge>{a.actor_type}</Badge>
                        {a.actor_id && <div className="text-[0.74rem] text-faint">{a.actor_id}</div>}
                      </td>
                      <td className="py-2">
                        {a.target_type}
                        {a.target_id && <div className="text-[0.74rem] text-faint">{a.target_id}</div>}
                      </td>
                      <td className="max-w-md py-2 pr-4">
                        {a.reason && <div>{a.reason}</div>}
                        {a.details && Object.keys(a.details).length > 0 && <code className="block truncate text-[0.74rem] text-muted" title={JSON.stringify(a.details)}>{JSON.stringify(a.details)}</code>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {audit.data && <Pagination page={page} pageSize={50} total={audit.data.total} onPage={(p) => setF({ page: String(p) })} />}
        </>
      ) : (
        <>
          <div className="mb-4">
            <Select value={f.status} onChange={(e) => setF({ status: e.target.value, page: "1" })} className="w-auto" aria-label="Status">
              <option value="">Any status</option>
              <option value="processed">Processed</option>
              <option value="failed">Failed</option>
              <option value="ignored">Ignored</option>
              <option value="received">Received</option>
            </Select>
          </div>
          {events.isError ? (
            <ErrorState error={events.error} />
          ) : !events.data ? (
            <LoadingRows rows={6} />
          ) : !events.data.items.length ? (
            <EmptyState title="No webhook events">Verified Paddle events appear here. Events with invalid signatures are rejected and counted on the System page.</EmptyState>
          ) : (
            <div className="overflow-x-auto rounded-[var(--radius-panel)] border border-rule bg-surface">
              <table className="w-full min-w-[760px] text-sm">
                <thead className="border-b border-rule text-left text-[0.8rem] text-muted">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">Event</th>
                    <th className="py-2.5 font-medium">Status</th>
                    <th className="py-2.5 font-medium">Occurred</th>
                    <th className="py-2.5 font-medium">Received</th>
                    <th className="py-2.5 pr-4 font-medium">Error</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-rule">
                  {events.data.items.map((e) => (
                    <tr key={e.event_id}>
                      <td className="px-4 py-2">
                        <div className="font-medium">{e.event_type}</div>
                        <code className="text-[0.74rem] text-faint">{e.event_id}</code>
                      </td>
                      <td className="py-2">
                        <Badge tone={e.status === "processed" ? "ok" : e.status === "failed" ? "danger" : "neutral"}>{e.status}</Badge>
                      </td>
                      <td className="py-2 text-muted">{formatDateTime(e.occurred_at)}</td>
                      <td className="py-2 text-muted">{formatDateTime(e.received_at)}</td>
                      <td className="py-2 pr-4 text-danger">{e.error}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {events.data && <Pagination page={page} pageSize={50} total={events.data.total} onPage={(p) => setF({ page: String(p) })} />}
        </>
      )}
    </>
  );
}

export default function AdminAuditPage() {
  return (
    <Suspense fallback={<LoadingRows rows={8} />}>
      <AuditView />
    </Suspense>
  );
}
