"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Checkbox, Select } from "@/components/ui/field";
import { Badge, EmptyState, ErrorState, LoadingRows, PageHeader, Pagination, Section, Stat } from "@/components/ui/misc";
import { useUrlState } from "@/hooks/use-url-state";
import { get, post } from "@/lib/api";
import { formatNumber, relativeTime } from "@/lib/format";
import type { AdminJob, QueueStats } from "@/types/admin";
import type { Paged } from "@/types/api";

const TONE: Record<string, "ok" | "warn" | "danger" | "action" | "neutral"> = { completed: "ok", failed: "danger", retrying: "warn", running: "action", queued: "neutral", cancelled: "neutral" };
const TYPES = ["ingest.file", "graph.extract", "research.run", "file.delete", "account.delete", "account.export", "reindex.embed", "maintenance.reconcile", "maintenance.cleanup_exports"];

function JobsView() {
  const qc = useQueryClient();
  const [f, setF] = useUrlState({ status: "", type: "", stuck: "", page: "1" });
  const page = Number(f.page) || 1;
  const jobs = useQuery({
    queryKey: ["admin-jobs", f],
    queryFn: () => get<Paged<AdminJob> & { stats: QueueStats }>("/admin/jobs", { status: f.status, type: f.type, stuck: f.stuck === "1", page, page_size: 30 }),
    refetchInterval: 5000,
    placeholderData: (p) => p,
  });
  const [action, setAction] = useState<{ job: AdminJob; kind: "retry" | "cancel" } | null>(null);
  const st = jobs.data?.stats;

  return (
    <>
      <PageHeader title="Jobs" description="Background ingestion, graph extraction, research, deletion and maintenance. Refreshes every few seconds." />
      {st && (
        <Section className="mb-6">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-6">
            <Stat label="Queued" value={formatNumber(st.by_status.queued ?? 0)} hint={st.oldest_queued_seconds ? `oldest ${Math.round(st.oldest_queued_seconds)}s` : undefined} />
            <Stat label="Running" value={formatNumber(st.by_status.running ?? 0)} />
            <Stat label="Retrying" value={formatNumber(st.by_status.retrying ?? 0)} />
            <Stat label="Stuck" value={formatNumber(st.stuck)} hint="Lease expired" />
            <Stat label="Failed" value={formatNumber(st.by_status.failed ?? 0)} />
            <Stat label="Completed" value={formatNumber(st.by_status.completed ?? 0)} />
          </div>
        </Section>
      )}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select value={f.status} onChange={(e) => setF({ status: e.target.value, page: "1" })} className="w-auto" aria-label="Status">
          <option value="">Any status</option>
          {Object.keys(TONE).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
        <Select value={f.type} onChange={(e) => setF({ type: e.target.value, page: "1" })} className="w-auto" aria-label="Type">
          <option value="">Any type</option>
          {TYPES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
        <Checkbox checked={f.stuck === "1"} onChange={(e) => setF({ stuck: e.target.checked ? "1" : "", page: "1" })} label="Only stuck jobs" />
      </div>
      {jobs.isError ? (
        <ErrorState error={jobs.error} />
      ) : !jobs.data ? (
        <LoadingRows rows={6} />
      ) : !jobs.data.items.length ? (
        <EmptyState title="No jobs match" />
      ) : (
        <div className="overflow-x-auto rounded-[var(--radius-panel)] border border-rule bg-surface">
          <table className="w-full min-w-[820px] text-sm">
            <thead className="border-b border-rule text-left text-[0.8rem] text-muted">
              <tr>
                <th className="px-4 py-2.5 font-medium">Job</th>
                <th className="py-2.5 font-medium">Status</th>
                <th className="py-2.5 font-medium">Attempts</th>
                <th className="py-2.5 font-medium">Error</th>
                <th className="py-2.5 font-medium">Updated</th>
                <th />
              </tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {jobs.data.items.map((j) => (
                <tr key={j.id} className="align-top">
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{j.type}</div>
                    <code className="text-[0.74rem] text-faint">{j.id}</code>
                    {j.account_id && (
                      <div>
                        <Link href={`/admin/users/${j.account_id}`} className="text-[0.78rem] text-action hover:underline">
                          Account
                        </Link>
                      </div>
                    )}
                  </td>
                  <td className="py-2.5">
                    <Badge tone={TONE[j.status] || "neutral"}>{j.status}</Badge>
                    {typeof j.progress?.phase === "string" && j.status === "running" && <div className="text-[0.78rem] text-muted">{j.progress.phase}</div>}
                  </td>
                  <td className="py-2.5 tabular-nums">
                    {j.attempts}/{j.max_attempts}
                  </td>
                  <td className="max-w-xs py-2.5">
                    {j.error ? (
                      <>
                        <code className="text-[0.76rem]">{j.error.code}</code>
                        <div className="line-clamp-2 text-[0.8rem] text-muted">{j.error.message}</div>
                      </>
                    ) : (
                      <span className="text-faint">None</span>
                    )}
                  </td>
                  <td className="py-2.5 text-muted">{relativeTime(j.updated_at)}</td>
                  <td className="py-2.5 pr-3 text-right">
                    <div className="flex justify-end gap-1">
                      {j.retryable_by_admin && (
                        <Button size="sm" variant="secondary" onClick={() => setAction({ job: j, kind: "retry" })}>
                          Retry
                        </Button>
                      )}
                      {["queued", "running", "retrying"].includes(j.status) && !["file.delete", "account.delete"].includes(j.type) && (
                        <Button size="sm" variant="ghost" onClick={() => setAction({ job: j, kind: "cancel" })}>
                          Cancel
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {jobs.data && <Pagination page={page} pageSize={30} total={jobs.data.total} onPage={(p) => setF({ page: String(p) })} />}
      <ConfirmDialog
        open={!!action}
        onOpenChange={(v) => !v && setAction(null)}
        title={action?.kind === "retry" ? "Retry this job?" : "Cancel this job?"}
        description={
          action?.kind === "retry"
            ? "The job runs again from its last checkpoint. Handlers are idempotent, so quotas aren't charged twice and records aren't duplicated."
            : "The job stops at its next safe checkpoint. Any quota it reserved is released."
        }
        confirmLabel={action?.kind === "retry" ? "Retry job" : "Cancel job"}
        danger={action?.kind === "cancel"}
        requireReason
        onConfirm={async (reason) => {
          const r = await post<{ status: string }>(`/admin/jobs/${action!.job.id}/${action!.kind}`, { reason });
          toast(`Job ${r.status}.`);
          qc.invalidateQueries({ queryKey: ["admin-jobs"] });
        }}
      />
    </>
  );
}

export default function AdminJobsPage() {
  return (
    <Suspense fallback={<LoadingRows rows={6} />}>
      <JobsView />
    </Suspense>
  );
}
