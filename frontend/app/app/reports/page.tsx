"use client";

import { useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { ButtonLink } from "@/components/ui/button";
import { Badge, EmptyState, ErrorState, LoadingRows, Meta, PageHeader, Pagination } from "@/components/ui/misc";
import { useUrlState } from "@/hooks/use-url-state";
import { get } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Paged, ReportSummary } from "@/types/api";

function ReportsView() {
  const [f, setF] = useUrlState({ page: "1" });
  const page = Number(f.page) || 1;
  const reports = useQuery({ queryKey: ["reports", page], queryFn: () => get<Paged<ReportSummary>>("/reports", { page, page_size: 20 }), placeholderData: (p) => p });
  return (
    <>
      <PageHeader title="Reports" description="Research reports written from your library. Every claim links to the passage that supports it." actions={<ButtonLink href="/app/research">New research</ButtonLink>} />
      {reports.isError ? (
        <ErrorState error={reports.error} onRetry={() => reports.refetch()} />
      ) : reports.isLoading ? (
        <LoadingRows rows={5} />
      ) : !reports.data?.items.length ? (
        <EmptyState icon={<ScrollText className="size-6" />} title="No reports yet" action={<ButtonLink href="/app/research">Start deep research</ButtonLink>}>
          Reports appear here when a research run finishes.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-rule border-y border-rule">
          {reports.data.items.map((r) => (
            <li key={r.id}>
              <Link href={`/app/reports/${r.id}`} className="group block py-4">
                <div className="flex items-start justify-between gap-4">
                  <span className="text-[1.05rem] font-medium group-hover:underline">{r.title}</span>
                  {r.status === "incomplete" && <Badge tone="warn">Incomplete</Badge>}
                </div>
                <p className="mt-0.5 line-clamp-2 max-w-3xl text-sm text-ink-2">{r.question}</p>
                <Meta className="mt-1" items={[formatDate(r.created_at), `${r.evidence_count} cited passages`]} />
              </Link>
            </li>
          ))}
        </ul>
      )}
      {reports.data && <Pagination page={page} pageSize={20} total={reports.data.total} onPage={(p) => setF({ page: String(p) })} />}
    </>
  );
}

export default function ReportsPage() {
  return (
    <Suspense fallback={<LoadingRows rows={5} />}>
      <ReportsView />
    </Suspense>
  );
}
