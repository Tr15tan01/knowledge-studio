"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, BookmarkPlus, Download, Lock, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import { Button, IconButton } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Badge, ErrorState, LoadingRows, Meta, Notice } from "@/components/ui/misc";
import { hasFeature, useUsage } from "@/features/auth/session";
import { CitationProvider, CitedText, SourceList, type CiteRef } from "@/features/citations/citations";
import { STOP_REASON } from "@/features/research/run-progress";
import { ApiError, apiFetch, del, get, post } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Report } from "@/types/api";

const CITE = /\[((?:E\d+)(?:\s*,\s*E\d+)*)\]/g;

function citedChunkIds(text: string, cites: CiteRef[]): string[] {
  const keys = new Set<string>();
  for (const m of text.matchAll(CITE)) m[1]!.split(/\s*,\s*/).forEach((k) => keys.add(k));
  return cites.filter((c) => keys.has(c.key)).map((c) => c.chunk_id);
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="mb-3 text-[1.3rem] font-semibold tracking-[-0.015em]">{title}</h2>
      {children}
    </section>
  );
}

export default function ReportPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const usage = useUsage();
  const report = useQuery({ queryKey: ["report", id], queryFn: () => get<Report>(`/reports/${id}`) });
  const [deleting, setDeleting] = useState(false);
  const [exporting, setExporting] = useState<"md" | "pdf" | null>(null);
  const pdf = hasFeature(usage.data, "pdf_export");
  const md = hasFeature(usage.data, "markdown_export");

  if (report.isError) return <ErrorState error={report.error} onRetry={() => report.refetch()} />;
  if (!report.data) return <LoadingRows rows={8} />;
  const r = report.data;
  const cites: CiteRef[] = r.evidence.map((e) => ({ key: e.label, chunk_id: e.chunk_id, source_title: e.source_title, citation_label: e.citation_label, section_path: e.section_path, snippet: e.snippet, file_id: e.file_id }));
  const cited = cites.filter((_, i) => r.evidence[i]!.cited);

  async function download(kind: "md" | "pdf") {
    setExporting(kind);
    try {
      const res = await apiFetch(`/reports/${id}/export.${kind}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${r.title.replace(/[^\w\s-]/g, "").slice(0, 60) || "report"}.${kind}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Export failed.");
    } finally {
      setExporting(null);
    }
  }

  async function save(text: string) {
    try {
      await post("/findings", { text: text.replace(CITE, "").trim(), report_id: id, chunk_ids: citedChunkIds(text, cites).slice(0, 20) });
      toast("Saved to findings.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't save.");
    }
  }

  const s = r.sections;
  return (
    <CitationProvider>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <Link href={r.project_id ? `/app/research/${r.project_id}` : "/app/reports"} className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
          <ArrowLeft className="size-4" /> Research project
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" onClick={() => download("md")} loading={exporting === "md"} disabled={!md}>
            <Download className="size-4" /> Markdown
          </Button>
          <Button size="sm" variant="secondary" onClick={() => download("pdf")} loading={exporting === "pdf"} disabled={!pdf} title={pdf ? undefined : "PDF export is part of Premium"}>
            {pdf ? <Download className="size-4" /> : <Lock className="size-4" />} PDF
          </Button>
          <IconButton label="Delete report" onClick={() => setDeleting(true)}>
            <Trash2 className="size-4" />
          </IconButton>
        </div>
      </div>

      <article className="mx-auto max-w-[760px]">
        <header>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {r.status === "incomplete" ? <Badge tone="warn">Incomplete report</Badge> : <Badge tone="ok">Complete</Badge>}
            <Meta items={[formatDateTime(r.created_at), `${cited.length} cited passages`]} />
          </div>
          <h1 className="text-[2.2rem] font-semibold leading-[1.1] tracking-[-0.025em]">{r.title}</h1>
          <p className="mt-3 text-[1.02rem] text-ink-2">
            <span className="text-muted">Question:</span> {r.question}
          </p>
        </header>

        {r.status === "incomplete" && (
          <Notice tone="warn" className="mt-6" icon={<AlertTriangle className="size-4" />}>
            This report is incomplete because {STOP_REASON[r.incomplete_reason || ""] || r.incomplete_reason}. It contains only what the gathered evidence supports.
          </Notice>
        )}
        {r.degraded?.length > 0 && <Notice tone="warn" className="mt-3">Some retrieval methods were unavailable while this report was written: {r.degraded.join(", ")}.</Notice>}

        {s.executive_summary && (
          <Block title="Executive summary">
            <CitedText text={s.executive_summary} cites={cites} />
          </Block>
        )}

        {r.research_questions.length > 0 && (
          <Block title="Research questions">
            <ol className="prose-read list-decimal pl-6">
              {r.research_questions.map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ol>
          </Block>
        )}

        {s.findings?.length > 0 && (
          <Block title="Main findings">
            <div className="space-y-7">
              {s.findings.map((f, i) => (
                <div key={i} className="group">
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="text-[1.08rem] font-semibold">{f.heading}</h3>
                    <div className="flex shrink-0 items-center gap-1">
                      {f.kind === "hypothesis" ? <Badge tone="warn">Hypothesis, not directly supported</Badge> : <Badge tone="ok">Supported by sources</Badge>}
                      <IconButton label="Save to findings" className="opacity-0 group-hover:opacity-100 focus:opacity-100" onClick={() => save(`${f.heading}: ${f.body}`)}>
                        <BookmarkPlus className="size-4" />
                      </IconButton>
                    </div>
                  </div>
                  <CitedText text={f.body} cites={cites} className={f.kind === "hypothesis" ? "mt-1.5 text-ink-2 italic" : "mt-1.5"} />
                </div>
              ))}
            </div>
          </Block>
        )}

        {(r.conflicts.length > 0 || s.contradictions?.length > 0) && (
          <Block title="Contradictory evidence">
            <ul className="prose-read list-disc space-y-2 pl-6">
              {r.conflicts.map((c, i) => (
                <li key={`c${i}`}>
                  <CitedText inline text={`${c.description} ${c.evidence.length ? `[${c.evidence.join(", ")}]` : ""}`} cites={cites} />
                </li>
              ))}
              {s.contradictions
                ?.filter((t) => !r.conflicts.some((c) => c.description === t))
                .map((t, i) => (
                  <li key={`t${i}`}>
                    <CitedText inline text={t} cites={cites} />
                  </li>
                ))}
            </ul>
          </Block>
        )}

        {s.cross_source_connections?.length > 0 && (
          <Block title="Connections across sources">
            <ul className="prose-read list-disc space-y-2 pl-6">
              {s.cross_source_connections.map((t, i) => (
                <li key={i}>
                  <CitedText inline text={t} cites={cites} />
                </li>
              ))}
            </ul>
          </Block>
        )}

        {s.limitations?.length > 0 && (
          <Block title="Limitations">
            <ul className="prose-read list-disc space-y-2 pl-6">
              {s.limitations.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          </Block>
        )}

        {s.open_questions?.length > 0 && (
          <Block title="Open questions">
            <ul className="prose-read list-disc space-y-2 pl-6">
              {s.open_questions.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          </Block>
        )}

        <Block title="References">
          <SourceList cites={cited} title="Passages cited in this report" />
          {cites.length > cited.length && <p className="mt-3 text-[0.82rem] text-muted">{cites.length - cited.length} more passages were gathered but not cited.</p>}
        </Block>

        <p className="mt-12 border-t border-rule pt-4 text-[0.82rem] text-muted">
          Generated from your library. Statements marked as hypotheses are interpretations, not claims made by your sources. Citations were checked against the passages
          gathered for this report; any that didn&apos;t match were removed.
        </p>
      </article>

      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete this report?"
        description="The report is removed. Findings you saved from it stay."
        confirmLabel="Delete report"
        danger
        onConfirm={async () => {
          await del(`/reports/${id}`);
          qc.invalidateQueries({ queryKey: ["reports"] });
          router.push("/app/reports");
        }}
      />
    </CitationProvider>
  );
}
