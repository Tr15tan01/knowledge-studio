"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NotebookPen, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";
import { IconButton } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { EmptyState, ErrorState, LoadingRows, Meta, PageHeader, Pagination } from "@/components/ui/misc";
import { CitationProvider, CiteChip } from "@/features/citations/citations";
import { useDebounced, useUrlState } from "@/hooks/use-url-state";
import { ApiError, del, get } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Finding, Paged } from "@/types/api";

function FindingsView() {
  const qc = useQueryClient();
  const [f, setF] = useUrlState({ q: "", page: "1" });
  const [q, setQ] = useState(f.q);
  const dq = useDebounced(q);
  useEffect(() => {
    if (dq !== f.q) setF({ q: dq, page: "1" });
  }, [dq]); // eslint-disable-line react-hooks/exhaustive-deps
  const page = Number(f.page) || 1;
  const list = useQuery({ queryKey: ["findings", f], queryFn: () => get<Paged<Finding>>("/findings", { q: f.q, page, page_size: 30 }), placeholderData: (p) => p });

  async function remove(id: string) {
    try {
      await del(`/findings/${id}`);
      qc.invalidateQueries({ queryKey: ["findings"] });
      toast("Finding removed.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't remove.");
    }
  }

  return (
    <CitationProvider>
      <PageHeader title="Saved findings" description="Answers and report findings you've kept, with the passages that support them." />
      <div className="relative mb-5 max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search findings" className="pl-9" aria-label="Search findings" />
      </div>
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isLoading ? (
        <LoadingRows rows={4} />
      ) : !list.data?.items.length ? (
        <EmptyState icon={<NotebookPen className="size-6" />} title={f.q ? "No findings match" : "Nothing saved yet"}>
          Use the bookmark button on an answer in Ask AI or on a finding in a report to keep it here.
        </EmptyState>
      ) : (
        <ul className="grid gap-x-10 gap-y-2 md:grid-cols-2">
          {list.data.items.map((it) => (
            <li key={it.id} className="group border-b border-rule py-4">
              <div className="flex items-start gap-3">
                <p className="prose-read min-w-0 flex-1 text-[1rem]">
                  {it.text}
                  {it.citations.map((c, i) => (
                    <CiteChip key={c.chunk_id} n={i + 1} cite={{ key: c.chunk_id, chunk_id: c.chunk_id, source_title: c.source_title, citation_label: c.citation_label }} />
                  ))}
                </p>
                <IconButton label="Remove finding" className="opacity-0 group-hover:opacity-100 focus:opacity-100" onClick={() => remove(it.id)}>
                  <Trash2 className="size-4" />
                </IconButton>
              </div>
              {it.note && <p className="mt-2 text-sm text-ink-2">{it.note}</p>}
              <Meta
                className="mt-2"
                items={[
                  formatDate(it.created_at),
                  [...new Set(it.citations.map((c) => c.source_title).filter(Boolean))].slice(0, 2).join(", ") || null,
                  it.report_id ? (
                    <Link href={`/app/reports/${it.report_id}`} className="hover:text-ink hover:underline">
                      From a report
                    </Link>
                  ) : (
                    "From Ask AI"
                  ),
                ]}
              />
            </li>
          ))}
        </ul>
      )}
      {list.data && <Pagination page={page} pageSize={30} total={list.data.total} onPage={(p) => setF({ page: String(p) })} />}
    </CitationProvider>
  );
}

export default function FindingsPage() {
  return (
    <Suspense fallback={<LoadingRows rows={4} />}>
      <FindingsView />
    </Suspense>
  );
}
