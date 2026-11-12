"use client";

import { useQuery } from "@tanstack/react-query";
import { BookOpen, FileText, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ButtonLink } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import { EmptyState, ErrorState, LoadingRows, Meta, PageHeader, Pagination } from "@/components/ui/misc";
import { useDebounced, useUrlState } from "@/hooks/use-url-state";
import { get } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Paged, Source } from "@/types/api";

const LANGS: Record<string, string> = { en: "English", ka: "Georgian", de: "German", fr: "French", es: "Spanish", ru: "Russian", it: "Italian" };

export function SourcesList({ kind }: { kind: "book" | "document" }) {
  const [f, setF] = useUrlState({ q: "", author: "", language: "", page: "1" });
  const [q, setQ] = useState(f.q);
  const [author, setAuthor] = useState(f.author);
  const dq = useDebounced(q);
  const da = useDebounced(author);
  useEffect(() => {
    if (dq !== f.q || da !== f.author) setF({ q: dq, author: da, page: "1" });
  }, [dq, da]); // eslint-disable-line react-hooks/exhaustive-deps
  const page = Number(f.page) || 1;
  const path = kind === "book" ? "/books" : "/documents";
  const res = useQuery({
    queryKey: [path, f],
    queryFn: () => get<Paged<Source>>(path, { page, page_size: 24, q: f.q, author: f.author, language: f.language }),
    placeholderData: (p) => p,
  });
  const Icon = kind === "book" ? BookOpen : FileText;
  const filtered = f.q || f.author || f.language;

  return (
    <>
      <PageHeader
        title={kind === "book" ? "Books" : "Documents"}
        description={
          kind === "book"
            ? "Long-form works with chapters. Each book is counted once, however many passages it has."
            : "Articles, notes, papers, spreadsheets and other files that aren't books."
        }
      />
      <div className="mb-5 flex flex-wrap gap-2">
        <div className="relative min-w-52 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by title" className="pl-9" aria-label="Search by title" />
        </div>
        <Input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="Author" className="w-48" aria-label="Filter by author" />
        <Select value={f.language} onChange={(e) => setF({ language: e.target.value, page: "1" })} className="w-auto" aria-label="Language">
          <option value="">Any language</option>
          {Object.entries(LANGS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </Select>
      </div>
      {res.isError ? (
        <ErrorState error={res.error} onRetry={() => res.refetch()} />
      ) : res.isLoading ? (
        <LoadingRows rows={6} />
      ) : !res.data?.items.length ? (
        <EmptyState
          icon={<Icon className="size-6" />}
          title={filtered ? `No ${kind}s match` : `No ${kind}s yet`}
          action={!filtered && <ButtonLink href="/app/library">Upload in Library</ButtonLink>}
        >
          {filtered ? "Try a different title, author or language." : kind === "book" ? "PDFs and DOCX files with chapters are detected as books. You can also choose “Book” when uploading." : "Upload notes, articles or data files."}
        </EmptyState>
      ) : (
        <ul className="grid gap-x-8 sm:grid-cols-2">
          {res.data.items.map((s) => (
            <li key={s.id} className="border-b border-rule">
              <Link href={`/app/library/${s.file_id}`} className="group flex gap-4 py-4">
                <span
                  className="grid h-16 w-12 shrink-0 place-items-center rounded-[4px] border border-rule bg-surface text-faint shadow-[2px_2px_0_var(--rule)]"
                  aria-hidden
                >
                  <Icon className="size-5" />
                </span>
                <span className="min-w-0">
                  <span className="block truncate font-medium group-hover:underline">{s.title || "Untitled"}</span>
                  <span className="block truncate text-sm text-ink-2">{s.author || <span className="text-faint">Author unknown</span>}</span>
                  <Meta
                    className="mt-1"
                    items={[s.page_count ? `${s.page_count} pages` : null, s.language ? LANGS[s.language] || s.language : null, `${s.chunk_count} passages`, `Added ${formatDate(s.created_at)}`]}
                  />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {res.data && <Pagination page={page} pageSize={24} total={res.data.total} onPage={(p) => setF({ page: String(p) })} />}
    </>
  );
}
