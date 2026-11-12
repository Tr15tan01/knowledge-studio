"use client";

import { useQuery } from "@tanstack/react-query";
import { BookOpen, MessageSquareQuote, Sparkles, Upload } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Button, ButtonLink } from "@/components/ui/button";
import { Badge, EmptyState, ErrorState, LoadingRows, Meta, Section, Skeleton, Stat } from "@/components/ui/misc";
import { UsageMeters } from "@/features/account/usage-meters";
import { useUsage, useUser } from "@/features/auth/session";
import { ActivityChart } from "@/features/dashboard/activity-chart";
import { UploadDialog } from "@/features/library/upload-dialog";
import { FileStatus, isProcessing } from "@/features/library/status";
import { get } from "@/lib/api";
import { formatDate, formatNumber, relativeTime } from "@/lib/format";
import type { BillingResponse, FileItem, LibraryStats, Paged, ReportSummary } from "@/types/api";

export default function DashboardPage() {
  const user = useUser();
  const [uploadOpen, setUploadOpen] = useState(false);
  const stats = useQuery({ queryKey: ["library-stats"], queryFn: () => get<LibraryStats>("/library/stats"), refetchInterval: (q) => ((q.state.data?.files_processing ?? 0) > 0 ? 4000 : false) });
  const usage = useUsage();
  const activity = useQuery({ queryKey: ["activity", 30], queryFn: () => get<{ days: { date: string; uploads: number; questions: number; reports: number }[] }>("/library/activity", { days: 30 }) });
  const recent = useQuery({
    queryKey: ["files", "recent"],
    queryFn: () => get<Paged<FileItem>>("/files", { page_size: 6, sort: "newest" }),
    refetchInterval: (q) => (q.state.data?.items.some(isProcessing) ? 3000 : false),
  });
  const reports = useQuery({ queryKey: ["reports", "recent"], queryFn: () => get<Paged<ReportSummary>>("/reports", { page_size: 4 }) });
  const billing = useQuery({ queryKey: ["billing"], queryFn: () => get<BillingResponse>("/billing/subscription") });

  const s = stats.data;
  const isNew = s && s.files_uploaded === 0 && s.files_processing === 0;
  const name = user?.display_name?.split(" ")[0];

  return (
    <>
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[2rem] font-semibold tracking-[-0.025em]">{name ? `Welcome back, ${name}` : "Your library"}</h1>
          <p className="mt-1 text-muted">
            {s ? (
              <>
                {formatNumber(s.books)} {s.books === 1 ? "book" : "books"}, {formatNumber(s.documents)} {s.documents === 1 ? "document" : "documents"}, {formatNumber(s.indexed_chunks)} searchable passages.
              </>
            ) : (
              <Skeleton className="inline-block h-4 w-64" />
            )}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setUploadOpen(true)}>
            <Upload className="size-4" /> Upload
          </Button>
          <ButtonLink href="/app/ask">
            <MessageSquareQuote className="size-4" /> Ask your library
          </ButtonLink>
        </div>
      </div>

      {stats.isError && <ErrorState error={stats.error} onRetry={() => stats.refetch()} />}

      {isNew && (
        <Section className="mb-6">
          <EmptyState
            icon={<BookOpen className="size-6" />}
            title="Start with one book or document"
            action={
              <Button onClick={() => setUploadOpen(true)}>
                <Upload className="size-4" /> Upload your first file
              </Button>
            }
          >
            Upload a PDF, DOCX, Markdown or text file. Once it&apos;s indexed you can ask questions and every answer will cite the page it came from.
          </EmptyState>
        </Section>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-6">
          <Section>
            <div className="grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-4">
              <Stat label="Files uploaded" value={s ? formatNumber(s.files_uploaded) : "—"} href="/app/library" />
              <Stat label="Books" value={s ? formatNumber(s.books) : "—"} href="/app/books" />
              <Stat label="Documents" value={s ? formatNumber(s.documents) : "—"} href="/app/documents" />
              <Stat label="Processing" value={s ? formatNumber(s.files_processing) : "—"} href="/app/library?status=processing" />
              <Stat label="Need attention" value={s ? formatNumber(s.files_need_attention) : "—"} href="/app/library?status=attention" />
              <Stat label="Indexed passages" value={s ? formatNumber(s.indexed_chunks) : "—"} />
              <Stat label="Graph entities" value={s ? formatNumber(s.entities) : "—"} href="/app/graph" />
              <Stat label="Research reports" value={s ? formatNumber(s.reports) : "—"} href="/app/reports" />
            </div>
          </Section>

          <Section title="Activity">{activity.data ? <ActivityChart days={activity.data.days} /> : <Skeleton className="h-64 w-full" />}</Section>

          <Section
            title="Recently added"
            actions={
              <Link href="/app/library" className="text-sm text-action hover:underline">
                Open library
              </Link>
            }
          >
            {recent.isLoading ? (
              <LoadingRows rows={3} />
            ) : recent.data?.items.length ? (
              <ul className="-my-2 divide-y divide-rule">
                {recent.data.items.map((f) => (
                  <li key={f.id} className="flex items-center gap-3 py-2.5">
                    <Link href={`/app/library/${f.id}`} className="min-w-0 flex-1">
                      <span className="block truncate font-medium hover:underline">{f.source?.title || f.title || f.filename}</span>
                      <Meta items={[f.kind === "book" ? "Book" : f.kind === "document" ? "Document" : "File", f.file_type.toUpperCase(), f.size_display, relativeTime(f.created_at)]} />
                    </Link>
                    <FileStatus file={f} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">Nothing here yet.</p>
            )}
          </Section>
        </div>

        <div className="space-y-6">
          <Section
            title="This period"
            description={usage.data ? `${usage.data.entitlements.plan.name} plan, ${formatDate(usage.data.entitlements.period.start)} to ${formatDate(usage.data.entitlements.period.end)}` : undefined}
          >
            {usage.data ? <UsageMeters usage={usage.data} showStorageDetail={false} /> : <LoadingRows rows={4} />}
          </Section>

          <Section title="Subscription">
            {billing.data ? (
              <div className="space-y-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{billing.data.entitlements.plan.name}</span>
                  <Badge tone={billing.data.entitlements.subscription_status === "active" ? "ok" : "warn"}>{billing.data.entitlements.subscription_status.replace(/_/g, " ")}</Badge>
                </div>
                {billing.data.subscription?.current_period_end && (
                  <p className="text-muted">
                    {billing.data.subscription.cancels_at_period_end ? "Ends" : "Renews"} {formatDate(billing.data.subscription.current_period_end)}
                  </p>
                )}
                {!billing.data.billing_enabled && <p className="text-muted">Billing is disabled on this server.</p>}
                <Link href="/app/billing" className="inline-block text-action hover:underline">
                  Usage & billing
                </Link>
              </div>
            ) : (
              <LoadingRows rows={2} />
            )}
          </Section>

          <Section
            title="Recent reports"
            actions={
              <Link href="/app/research" className="text-sm text-action hover:underline">
                New research
              </Link>
            }
          >
            {reports.data?.items.length ? (
              <ul className="-my-1.5 space-y-0.5">
                {reports.data.items.map((r) => (
                  <li key={r.id}>
                    <Link href={`/app/reports/${r.id}`} className="-mx-2 block rounded-md px-2 py-1.5 hover:bg-surface-2">
                      <span className="block truncate text-sm font-medium">{r.title}</span>
                      <Meta className="text-[0.78rem]" items={[r.status === "incomplete" ? "Incomplete" : null, `${r.evidence_count} sources`, relativeTime(r.created_at)]} />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="text-sm text-muted">
                <Sparkles className="mb-1.5 size-4 text-faint" />
                Deep research reads across your library and writes a cited report.
              </div>
            )}
          </Section>
        </div>
      </div>
      <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} />
    </>
  );
}
