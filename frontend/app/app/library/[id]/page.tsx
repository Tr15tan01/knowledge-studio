"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, MessageSquareQuote, Pencil, RotateCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Button, ButtonLink } from "@/components/ui/button";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/field";
import { Badge, ErrorState, LoadingRows, Notice, PageHeader, Section } from "@/components/ui/misc";
import { qk } from "@/features/auth/session";
import { FileStatus, isProcessing, progressLabel } from "@/features/library/status";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import type { FileItem, MetaValue, Source } from "@/types/api";

const PROVENANCE: Record<string, { label: string; tone: "neutral" | "action" | "warn" }> = {
  extracted: { label: "From file", tone: "neutral" },
  detected: { label: "Detected", tone: "neutral" },
  user: { label: "Edited by you", tone: "action" },
  filename: { label: "From filename", tone: "warn" },
  estimated: { label: "Estimated", tone: "warn" },
};

function MetaRow({ label, value, meta }: { label: string; value: string | number | null | undefined; meta?: MetaValue | null }) {
  const p = meta ? PROVENANCE[meta.provenance] : null;
  return (
    <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-3 py-2.5 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className="flex flex-wrap items-center gap-2">
        {value !== null && value !== undefined && value !== "" ? <span>{value}</span> : <span className="text-faint">Unknown</span>}
        {p && value ? <Badge tone={p.tone}>{p.label}</Badge> : null}
      </dd>
    </div>
  );
}

export default function FileDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const file = useQuery({
    queryKey: ["file", id],
    queryFn: () => get<FileItem>(`/files/${id}`),
    refetchInterval: (q) => (q.state.data && isProcessing(q.state.data) ? 2500 : false),
  });

  const retry = useMutation({
    mutationFn: () => post(`/files/${id}/retry`),
    onSuccess: () => {
      toast("Processing restarted.");
      qc.invalidateQueries({ queryKey: ["file", id] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Retry failed."),
  });

  if (file.isError) return <ErrorState error={file.error} onRetry={() => file.refetch()} />;
  if (!file.data) return <LoadingRows rows={6} />;
  const f = file.data;
  const src = f.source;
  const md = src?.metadata || {};
  const progress = progressLabel(f.progress);

  return (
    <>
      <PageHeader
        back={
          <Link href="/app/library" className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
            <ArrowLeft className="size-4" /> Library
          </Link>
        }
        title={src?.title || f.title || f.filename}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <FileStatus file={f} />
            <span>{f.kind === "book" ? "Book" : f.kind === "document" ? "Document" : "Type pending"}</span>
          </span>
        }
        actions={
          <>
            {f.processing_status === "indexed" && (
              <ButtonLink href={`/app/ask?file=${f.id}`}>
                <MessageSquareQuote className="size-4" /> Ask about this
              </ButtonLink>
            )}
            {f.original_available && (
              <a href={`/api/v1/files/${f.id}/download`} className="inline-flex h-10 items-center gap-2 rounded-[var(--radius-control)] border border-rule-strong bg-surface px-4 text-[0.94rem] font-medium hover:bg-surface-2">
                <Download className="size-4" /> Download
              </a>
            )}
            <Button variant="ghost" onClick={() => setDeleting(true)} aria-label="Delete file">
              <Trash2 className="size-4" />
            </Button>
          </>
        }
      />

      {(f.processing_status === "failed" || f.processing_status === "rejected") && (
        <Notice tone="danger" className="mb-6">
          <p className="font-medium">{f.processing_error?.message || f.job?.error?.message || "Processing failed."}</p>
          {f.processing_status === "failed" && (
            <Button size="sm" variant="secondary" className="mt-2" loading={retry.isPending} onClick={() => retry.mutate()}>
              <RotateCw className="size-3.5" /> Retry processing
            </Button>
          )}
        </Notice>
      )}
      {isProcessing(f) && (
        <Notice tone="action" className="mb-6">
          {progress || "Waiting for a worker"}. This page updates automatically.
        </Notice>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Section
          title="Details"
          actions={
            src && (
              <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
                <Pencil className="size-3.5" /> Edit
              </Button>
            )
          }
        >
          <dl className="-my-2.5 divide-y divide-rule">
            <MetaRow label="Title" value={src?.title || f.title} meta={md.title} />
            <MetaRow label="Author" value={src?.author} meta={md.author} />
            <MetaRow label="Publisher" value={src?.publisher} meta={md.publisher} />
            <MetaRow label="Published" value={src?.publication_date} meta={md.publication_date} />
            <MetaRow label="Language" value={src?.language} meta={md.language} />
            <MetaRow label="Pages" value={src?.page_count} />
            <MetaRow label="License note" value={src?.license_note} />
          </dl>
          {src?.warnings?.length ? (
            <div className="mt-4">
              <Notice tone="warn">
                <p className="font-medium">Extraction notes</p>
                <ul className="mt-1 list-disc pl-4">
                  {src.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </Notice>
            </div>
          ) : null}
        </Section>

        <Section title="File">
          <dl className="-my-2.5 divide-y divide-rule">
            <MetaRow label="Filename" value={f.filename} />
            <MetaRow label="Format" value={f.file_type.toUpperCase()} />
            <MetaRow label="Size" value={f.size_display} />
            <MetaRow label="Passages" value={formatNumber(f.chunk_count)} />
            <MetaRow label="Uploaded" value={formatDateTime(f.created_at)} />
            <MetaRow label="Indexed" value={f.indexed_at ? formatDateTime(f.indexed_at) : null} />
          </dl>
          {f.processing_status === "indexed" && !f.original_available && (
            <p className="mt-4 text-[0.82rem] text-muted">The uploaded file isn&apos;t kept after indexing, so it can&apos;t be downloaded. Its text stays searchable here.</p>
          )}
        </Section>
      </div>

      {src?.outline?.length ? (
        <Section title={f.kind === "book" ? "Chapters" : "Sections"} className="mt-6">
          <ol className="-my-1 space-y-0.5">
            {src.outline.map((o, i) => (
              <li key={i} className="flex items-baseline justify-between gap-4 py-1.5 text-sm" style={{ paddingLeft: `${Math.max(0, o.level - 1) * 1.25}rem` }}>
                <span className={o.level === 1 ? "font-medium" : "text-ink-2"}>{o.title}</span>
                {o.page_start ? (
                  <span className="shrink-0 tabular-nums text-faint">
                    p. {o.page_start}
                    {o.page_end && o.page_end !== o.page_start ? `–${o.page_end}` : ""}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </Section>
      ) : null}

      {src && editing && <EditMetadata onOpenChange={setEditing} source={src} onSaved={() => qc.invalidateQueries({ queryKey: ["file", id] })} />}
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete this file?"
        description="The original file, its passages, embeddings and graph links are removed. This can't be undone."
        confirmLabel="Delete file"
        danger
        onConfirm={async () => {
          await del(`/files/${id}`);
          qc.invalidateQueries({ queryKey: ["files"] });
          qc.invalidateQueries({ queryKey: qk.usage });
          toast("Deleting. Storage is released once removal is confirmed.");
          router.push("/app/library");
        }}
      />
    </>
  );
}

/** Mounted only while open, so the form always starts from the saved metadata. */
function EditMetadata({ onOpenChange, source, onSaved }: { onOpenChange: (v: boolean) => void; source: Source; onSaved: () => void }) {
  const [form, setForm] = useState(() => ({
    title: source.title || "",
    author: source.author || "",
    publisher: source.publisher || "",
    publication_date: source.publication_date || "",
    license_note: source.license_note || "",
  }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, string> = {};
      for (const [k, v] of Object.entries(form)) if (v.trim() !== ((source as unknown as Record<string, string | null>)[k] || "")) body[k] = v.trim();
      if (Object.keys(body).length) await patch(`/sources/${source.id}`, body);
      onSaved();
      onOpenChange(false);
      toast("Details saved.");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Couldn't save.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      title="Edit details"
      description="Your edits are labelled as yours and are used in citations."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} loading={busy} disabled={!form.title.trim()}>
            Save details
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Title">{(p) => <Input {...p} value={form.title} onChange={set("title")} maxLength={200} />}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Author" optional>
            {(p) => <Input {...p} value={form.author} onChange={set("author")} maxLength={200} />}
          </Field>
          <Field label="Publisher" optional>
            {(p) => <Input {...p} value={form.publisher} onChange={set("publisher")} maxLength={200} />}
          </Field>
        </div>
        <Field label="Publication date" optional hint="Any format, for example 1998 or 2021-04.">
          {(p) => <Input {...p} value={form.publication_date} onChange={set("publication_date")} maxLength={40} />}
        </Field>
        <Field label="Copyright or license note" optional>
          {(p) => <Textarea {...p} value={form.license_note} onChange={set("license_note")} maxLength={1000} rows={3} />}
        </Field>
        {err && <p className="text-sm text-danger">{err}</p>}
      </div>
    </Dialog>
  );
}
