"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { Badge, ErrorState, LoadingRows, Notice, PageHeader, Section, UsageBar } from "@/components/ui/misc";
import { useReauth } from "@/features/auth/reauth";
import { hasRole, useUser } from "@/features/auth/session";
import { ApiError, get, post } from "@/lib/api";
import { formatDateTime, formatNumber, relativeTime, titleCase } from "@/lib/format";
import type { AdminConfig, AdminHealth, ReindexMigration } from "@/types/admin";

function Status({ ok, required = true }: { ok: boolean; required?: boolean }) {
  if (ok) return <CheckCircle2 className="size-4.5 text-ok" aria-label="Healthy" />;
  return required ? <XCircle className="size-4.5 text-danger" aria-label="Failing" /> : <CircleAlert className="size-4.5 text-warn" aria-label="Degraded" />;
}

function valueTone(v: string | number | boolean | null): "ok" | "warn" | "neutral" {
  if (v === "configured" || v === true) return "ok";
  if (v === "missing" || v === false) return "warn";
  return "neutral";
}

export default function AdminSystemPage() {
  const superAdmin = hasRole(useUser(), "SUPER_ADMIN");
  const health = useQuery({ queryKey: ["admin-health"], queryFn: () => get<AdminHealth>("/admin/health"), refetchInterval: 15_000 });
  const config = useQuery({ queryKey: ["admin-config"], queryFn: () => get<AdminConfig>("/admin/config") });
  const h = health.data;

  return (
    <>
      <PageHeader title="System" description="Live dependency checks and configuration status. Secret values are never shown and can only be changed in the server environment." />
      {health.isError && <ErrorState error={health.error} onRetry={() => health.refetch()} />}
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Health" actions={h && <Badge tone={h.ready ? "ok" : "danger"}>{h.ready ? "Ready" : "Not ready"}</Badge>}>
          {!h ? (
            <LoadingRows rows={5} />
          ) : (
            <ul className="-my-1 divide-y divide-rule text-sm">
              {Object.entries(h.checks).map(([k, c]) => (
                <li key={k} className="flex items-start gap-3 py-2.5">
                  <Status ok={c.ok} required={c.required} />
                  <div className="min-w-0 flex-1">
                    <div className="font-medium">{titleCase(k)}</div>
                    <div className="text-muted">{c.detail}</div>
                  </div>
                  {c.latency_ms !== undefined && <span className="tabular-nums text-faint">{c.latency_ms} ms</span>}
                </li>
              ))}
              {Object.entries(h.providers).map(([k, c]) => (
                <li key={k} className="flex items-start gap-3 py-2.5">
                  <Status ok={c.ok} />
                  <div>
                    <div className="font-medium">{k === "llm" ? "Generative AI provider" : "Embedding provider"}</div>
                    <div className="text-muted">{c.detail}</div>
                  </div>
                </li>
              ))}
              <li className="flex items-start gap-3 py-2.5">
                <Status ok={h.paddle.ok} required={false} />
                <div>
                  <div className="font-medium">Paddle</div>
                  <div className="text-muted">{h.paddle.detail}</div>
                </div>
              </li>
            </ul>
          )}
        </Section>

        <div className="space-y-6">
          <Section title="Billing webhooks (24 hours)">
            {h && (
              <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <div>
                  <dt className="text-muted">Processed</dt>
                  <dd className="text-lg font-semibold tabular-nums">{formatNumber(h.webhooks.processed_24h)}</dd>
                </div>
                <div>
                  <dt className="text-muted">Failed</dt>
                  <dd className={`text-lg font-semibold tabular-nums ${h.webhooks.failed_24h ? "text-danger" : ""}`}>{formatNumber(h.webhooks.failed_24h)}</dd>
                </div>
                <div>
                  <dt className="text-muted">Bad signatures</dt>
                  <dd className={`text-lg font-semibold tabular-nums ${h.webhooks.rejected_signatures_24h ? "text-warn" : ""}`}>{formatNumber(h.webhooks.rejected_signatures_24h)}</dd>
                </div>
                <div>
                  <dt className="text-muted">Last received</dt>
                  <dd>{h.webhooks.last_received_at ? relativeTime(h.webhooks.last_received_at) : "Never"}</dd>
                </div>
              </dl>
            )}
          </Section>
          <Section title="Workers">
            {h && (h.workers.length === 0 ? (
              <Notice tone="danger">No worker has reported in. Start one with the worker command; uploads and research won&apos;t progress without it.</Notice>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {h.workers.map((w) => (
                  <li key={w.id} className="flex justify-between gap-3">
                    <code className="text-[0.8rem]">{w.id}</code>
                    <span className="text-muted">
                      {w.concurrency} slots, seen {relativeTime(w.seen_at)}
                    </span>
                  </li>
                ))}
              </ul>
            ))}
          </Section>
        </div>
      </div>

      <Section title="Configuration" className="mt-6" description={config.data?.note}>
        {!config.data ? (
          <LoadingRows rows={5} />
        ) : (
          <>
            {config.data.problems.length > 0 && (
              <Notice tone="danger" className="mb-4">
                <ul className="list-disc pl-4">
                  {config.data.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              </Notice>
            )}
            <div className="grid gap-x-10 gap-y-6 md:grid-cols-2 xl:grid-cols-3">
              {Object.entries(config.data.integrations).map(([group, values]) => (
                <div key={group}>
                  <h3 className="mb-2 text-sm font-semibold">{titleCase(group)}</h3>
                  <dl className="space-y-1.5 text-sm">
                    {Object.entries(values).map(([k, v]) => (
                      <div key={k} className="flex items-start justify-between gap-3">
                        <dt className="text-muted">{titleCase(k)}</dt>
                        <dd className="text-right">{typeof v === "boolean" || v === "configured" || v === "missing" ? <Badge tone={valueTone(v)}>{String(v === true ? "on" : v === false ? "off" : v)}</Badge> : <span className="break-all">{v ?? "—"}</span>}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
            <p className="mt-4 text-[0.8rem] text-muted">Environment: {config.data.environment}</p>
          </>
        )}
      </Section>

      <Reindex superAdmin={superAdmin} />
    </>
  );
}

function Reindex({ superAdmin }: { superAdmin: boolean }) {
  const qc = useQueryClient();
  const reauth = useReauth();
  const q = useQuery({
    queryKey: ["admin-reindex"],
    queryFn: () => get<{ items: ReindexMigration[]; active_space: string; active_vector_field: string }>("/admin/reindex"),
    refetchInterval: (x) => (x.state.data?.items.some((m) => !m.complete && m.status !== "failed") ? 5000 : false),
  });
  const [open, setOpen] = useState(false);
  const [reprocess, setReprocess] = useState(false);
  const [form, setForm] = useState({ model: "gemini-embedding-2", dimensions: "1536", vector_field: "embedding_v2", note: "" });
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    try {
      const r = await reauth(() => post<{ total_chunks: number }>("/admin/reindex", { provider: "gemini", model: form.model, dimensions: Number(form.dimensions), vector_field: form.vector_field, note: form.note }));
      toast(`Re-indexing ${formatNumber(r.total_chunks)} passages into a new vector field.`);
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["admin-reindex"] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't start.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      title="Embedding re-indexing"
      className="mt-6"
      description={q.data ? `Active space ${q.data.active_space} in field “${q.data.active_vector_field}”.` : undefined}
      actions={
        superAdmin && (
          <>
            <Button size="sm" variant="ghost" onClick={() => setReprocess(true)}>
              Reprocess outdated files
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
              Start migration
            </Button>
          </>
        )
      }
    >
      <p className="mb-4 text-sm text-muted">
        Changing the embedding model or dimensions never mixes vectors: new embeddings are written to a separate field while search keeps using the active one. Switch
        configuration (EMBEDDING settings and the Atlas index) only after the migration reports complete. See the runbook.
      </p>
      {!q.data ? (
        <LoadingRows rows={2} />
      ) : q.data.items.length === 0 ? (
        <p className="text-sm text-muted">No migrations yet.</p>
      ) : (
        <ul className="space-y-4">
          {q.data.items.map((m) => (
            <li key={m.id} className="text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">
                  {m.target.model}, {m.target.dimensions} dims → <code>{m.target.vector_field}</code>
                </span>
                <Badge tone={m.complete ? "ok" : m.status === "failed" ? "danger" : "action"}>{m.complete ? "complete" : m.status}</Badge>
              </div>
              <div className="mt-1.5">
                <UsageBar used={m.embedded} limit={m.total_chunks || 1} label="Migration progress" />
              </div>
              <div className="mt-1 text-[0.8rem] text-muted">
                {formatNumber(m.embedded)} of {formatNumber(m.total_chunks)} passages, started {formatDateTime(m.created_at)}. {m.note}
              </div>
            </li>
          ))}
        </ul>
      )}
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Start an embedding migration"
        description="Embeds every passage again with the new settings into a separate vector field. Old vectors stay searchable until you switch."
        footer={
          <Button onClick={start} loading={busy} disabled={form.note.trim().length < 3}>
            Start migration
          </Button>
        }
      >
        <div className="space-y-4">
          <Field label="Model">{(f) => <Input {...f} value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />}</Field>
          <Field label="Dimensions" hint="Between 128 and 3072. Recommended: 768, 1536 or 3072.">
            {(f) => <Input {...f} type="number" min={128} max={3072} value={form.dimensions} onChange={(e) => setForm({ ...form, dimensions: e.target.value })} />}
          </Field>
          <Field label="Target vector field" hint="Must differ from the active field. Create a matching Atlas Vector Search index for it.">
            {(f) => <Input {...f} value={form.vector_field} onChange={(e) => setForm({ ...form, vector_field: e.target.value })} />}
          </Field>
          <Field label="Note">{(f) => <Input {...f} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />}</Field>
          <Notice tone="warn">This calls the embedding provider for every passage in every library and can be costly.</Notice>
        </div>
      </Dialog>
      <ConfirmDialog
        open={reprocess}
        onOpenChange={setReprocess}
        title="Reprocess outdated files?"
        description="Queues files whose chunking version or embedding space differs from the current configuration. Runs at low priority."
        confirmLabel="Queue reprocessing"
        requireReason
        onConfirm={async (reason) => {
          const r = await reauth(() => post<{ queued: number }>("/admin/reprocess-files", { reason }));
          toast(`Queued ${r.queued} file(s).`);
        }}
      />
    </Section>
  );
}
