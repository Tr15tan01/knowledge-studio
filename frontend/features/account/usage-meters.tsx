"use client";

import Link from "next/link";
import { UsageBar } from "@/components/ui/misc";
import { formatBytes, formatDate, formatNumber } from "@/lib/format";
import type { UsageResponse } from "@/types/api";

function Row({ label, used, reserved, limit, display, resets }: { label: string; used: number; reserved: number; limit: number; display?: string; resets?: string | null }) {
  const remaining = Math.max(0, limit - used - reserved);
  return (
    <div className="py-3 first:pt-0 last:pb-0">
      <div className="mb-1.5 flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium">{label}</span>
        <span className="tabular-nums text-muted">{display ?? `${formatNumber(used)} of ${formatNumber(limit)}`}</span>
      </div>
      <UsageBar used={used} reserved={reserved} limit={limit} label={label} />
      <div className="mt-1 flex justify-between text-[0.78rem] text-faint">
        <span>{reserved > 0 ? `${formatNumber(reserved)} in progress, ` : ""}{formatNumber(remaining)} left</span>
        {resets && <span>Resets {formatDate(resets)}</span>}
      </div>
    </div>
  );
}

/** Storage panel and period quotas, all from /me/usage (server-calculated). */
export function UsageMeters({ usage, showStorageDetail = true }: { usage: UsageResponse; showStorageDetail?: boolean }) {
  const u = usage.usage;
  const s = usage.storage;
  return (
    <div className="divide-y divide-rule">
      <div className="pb-3">
        <div className="mb-1.5 flex items-baseline justify-between gap-3 text-sm">
          <span className="font-medium">Storage</span>
          <span className="tabular-nums text-muted" data-testid="storage-used">
            Used: {formatBytes(s.used_bytes)} of {formatBytes(s.limit_bytes)}
          </span>
        </div>
        <UsageBar used={s.used_bytes} reserved={s.reserved_bytes} limit={s.limit_bytes} label="Storage used" warnAt={s.warning_level ? 0 : 80} />
        <div className="mt-1 flex flex-wrap justify-between gap-2 text-[0.78rem] text-faint">
          <span>
            {formatBytes(s.available_bytes)} remaining, files up to {formatBytes(s.max_file_size_bytes)} each
          </span>
          <Link href="/app/library?sort=size" className="hover:text-ink hover:underline">
            Manage files
          </Link>
        </div>
        {showStorageDetail && (
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-[0.82rem] sm:grid-cols-4">
            <div>
              <dt className="text-muted">Books</dt>
              <dd className="tabular-nums">{formatBytes(s.breakdown.book_bytes)}</dd>
            </div>
            <div>
              <dt className="text-muted">Documents</dt>
              <dd className="tabular-nums">{formatBytes(s.breakdown.document_bytes)}</dd>
            </div>
            <div>
              <dt className="text-muted">Uploading</dt>
              <dd className="tabular-nums">{s.breakdown.uploading_files ? `${s.breakdown.uploading_files} (${formatBytes(s.breakdown.uploading_bytes)})` : "None"}</dd>
            </div>
            <div>
              <dt className="text-muted">Being deleted</dt>
              <dd className="tabular-nums">{s.breakdown.deleting_files || "None"}</dd>
            </div>
          </dl>
        )}
      </div>
      <Row label="Uploads this period" used={u.uploads.used} reserved={u.uploads.reserved} limit={u.uploads.limit} resets={u.uploads.resets_at} />
      <Row label="AI questions" used={u.questions.used} reserved={u.questions.reserved} limit={u.questions.limit} resets={u.questions.resets_at} />
      <Row label="Research reports" used={u.research_reports.used} reserved={u.research_reports.reserved} limit={u.research_reports.limit} resets={u.research_reports.resets_at} />
    </div>
  );
}
