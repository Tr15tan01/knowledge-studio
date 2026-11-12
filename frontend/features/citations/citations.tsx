"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { Fragment, createContext, useContext, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Dialog } from "@/components/ui/dialog";
import { ErrorState, LoadingRows, Meta } from "@/components/ui/misc";
import { get } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { Chunk, Citation } from "@/types/api";

/** Minimal shape needed to show and open a citation. */
export type CiteRef = {
  key: string; // marker as written in text, e.g. "S1" or "E3"
  chunk_id: string;
  source_title: string | null;
  citation_label: string | null;
  section_path?: string[];
  snippet?: string;
  file_id?: string;
};

export const citeRefs = (cs: Citation[]): CiteRef[] =>
  cs.map((c) => ({ key: c.marker, chunk_id: c.chunk_id, source_title: c.source_title, citation_label: c.citation_label, section_path: c.section_path, snippet: c.snippet, file_id: c.file_id }));

type Ctx = { open: (c: CiteRef) => void; active: string | null };
const PreviewCtx = createContext<Ctx>({ open: () => {}, active: null });

/** Provides a single source-preview sheet for every citation inside it. */
export function CitationProvider({ children }: { children: ReactNode }) {
  const [cur, setCur] = useState<CiteRef | null>(null);
  return (
    <PreviewCtx.Provider value={{ open: setCur, active: cur?.chunk_id ?? null }}>
      {children}
      <SourcePreview cite={cur} onClose={() => setCur(null)} />
    </PreviewCtx.Provider>
  );
}

export const useCitationPreview = () => useContext(PreviewCtx);

export function CiteChip({ cite, n }: { cite: CiteRef; n: number | string }) {
  const { open, active } = useCitationPreview();
  return (
    <button
      type="button"
      className="cite-chip"
      data-active={active === cite.chunk_id}
      onClick={() => open(cite)}
      aria-label={`Source ${n}: ${cite.source_title || "passage"}${cite.citation_label ? `, ${cite.citation_label}` : ""}`}
      title={`${cite.source_title || "Source"}${cite.citation_label ? `, ${cite.citation_label}` : ""}`}
    >
      {n}
    </button>
  );
}

const MARKER = /\[((?:[SE]\d+)(?:\s*,\s*[SE]\d+)*)\]/g;

/**
 * Renders model text as Markdown and turns validated markers like [S1] / [E2, E3] into
 * numbered citation chips. Markers that don't resolve to a validated citation are dropped,
 * so the UI never shows a citation the backend didn't verify.
 */
export function CitedText({ text, cites, className, inline }: { text: string; cites: CiteRef[]; className?: string; inline?: boolean }) {
  const byKey = new Map(cites.map((c) => [c.key, c]));
  const order = new Map(cites.map((c, i) => [c.key, i + 1]));
  const md = text.replace(MARKER, (_m, group: string) =>
    group
      .split(/\s*,\s*/)
      .filter((k) => byKey.has(k))
      .map((k) => `[${order.get(k)}](#cite-${k})`)
      .join(""),
  );
  return (
    <div className={cn(!inline && "prose-read", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // Model output is untrusted: no raw HTML, and only our own citation links are rendered as links.
        skipHtml
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("#cite-")) {
              const c = byKey.get(href.slice(6));
              return c ? <CiteChip cite={c} n={order.get(c.key) ?? "?"} /> : null;
            }
            return <span>{children}</span>;
          },
          img: () => null,
          p: ({ children }) => (inline ? <span>{children}</span> : <p>{children}</p>),
        }}
      >
        {md}
      </ReactMarkdown>
    </div>
  );
}

/** List of sources under an answer; each row can be opened in the preview sheet. */
export function SourceList({ cites, title = "Sources" }: { cites: CiteRef[]; title?: string }) {
  const { open, active } = useCitationPreview();
  if (!cites.length) return null;
  return (
    <div className="mt-4">
      <div className="mb-1.5 text-[0.8rem] font-medium text-muted">{title}</div>
      <ol className="space-y-1">
        {cites.map((c, i) => (
          <li key={c.key}>
            <button
              onClick={() => open(c)}
              className={cn("flex w-full items-start gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-surface-2", active === c.chunk_id && "bg-surface-2")}
            >
              <span className="cite-chip mt-0.5 shrink-0 !text-[0.7rem]">{i + 1}</span>
              <span className="min-w-0 text-sm">
                <span className="font-medium">{c.source_title || "Untitled source"}</span>
                {c.citation_label && <span className="text-muted">, {c.citation_label}</span>}
                {c.section_path?.length ? <span className="block truncate text-[0.8rem] text-faint">{c.section_path.join(" › ")}</span> : null}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

function SourcePreview({ cite, onClose }: { cite: CiteRef | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["chunk", cite?.chunk_id], queryFn: () => get<Chunk>(`/chunks/${cite!.chunk_id}`, { context: 1 }), enabled: !!cite });
  const c = q.data;
  const before = c?.neighbours.filter((n) => n.ordinal < c.ordinal) ?? [];
  const after = c?.neighbours.filter((n) => n.ordinal > c.ordinal) ?? [];
  const body = c ? c.text.slice(c.overlap_prefix_chars || 0) : "";
  return (
    <Dialog
      open={!!cite}
      onOpenChange={(v) => !v && onClose()}
      side="right"
      title={cite?.source_title || "Source"}
      description={
        <Meta items={[cite?.citation_label, c?.section_path?.length ? c.section_path.join(" › ") : null, c ? `Passage ${c.ordinal + 1}` : null]} />
      }
    >
      {q.isError ? (
        <ErrorState error={q.error} compact />
      ) : !c ? (
        <LoadingRows rows={5} />
      ) : (
        <div>
          <div className="prose-read text-[1rem]">
            {before.map((n) => (
              <p key={n.id} className="text-muted">
                {n.text}
              </p>
            ))}
            <p>
              {body.split(/\n{2,}/).map((para, i, arr) => (
                <Fragment key={i}>
                  <span className="mark">{para}</span>
                  {i < arr.length - 1 && (
                    <>
                      <br />
                      <br />
                    </>
                  )}
                </Fragment>
              ))}
            </p>
            {after.map((n) => (
              <p key={n.id} className="text-muted">
                {n.text}
              </p>
            ))}
          </div>
          <p className="mt-5 text-[0.82rem] text-muted">The highlighted passage is the exact text the answer cited. Surrounding passages are shown for context.</p>
          <Link href={`/app/library/${c.file_id}`} className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-action hover:underline">
            Open source details <ExternalLink className="size-3.5" />
          </Link>
        </div>
      )}
    </Dialog>
  );
}
