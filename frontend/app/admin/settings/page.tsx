"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { ErrorState, LoadingRows, Notice, PageHeader, Section } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";
import { useReauth } from "@/features/auth/reauth";
import { hasRole, useUser } from "@/features/auth/session";
import { ApiError, get, put } from "@/lib/api";
import { formatBytes, formatDateTime, formatNumber } from "@/lib/format";
import type { AdminSettings, RuntimeSettings, StorageBackend } from "@/types/admin";

const BACKENDS: Record<StorageBackend, { label: string; description: string }> = {
  local: {
    label: "Server disk",
    description: "Files are written to a folder on this server. The API and the worker must run on the same machine.",
  },
  mongodb: {
    label: "MongoDB",
    description: "Files are written to the database, so the API and workers can run on different servers. A file uses database space while it is held.",
  },
  s3: {
    label: "S3-compatible bucket",
    description: "Files are written to the bucket configured in the server environment.",
  },
};

export default function AdminSettingsPage() {
  const q = useQuery({ queryKey: ["admin-settings"], queryFn: () => get<AdminSettings>("/admin/settings"), staleTime: Infinity });
  return (
    <>
      <PageHeader title="Settings" description="How the service stores files, answers questions and accepts new users. Changes apply within a few seconds, without a restart." />
      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <LoadingRows rows={8} /> : <SettingsForm key={q.dataUpdatedAt} data={q.data} />}
    </>
  );
}

function SettingsForm({ data }: { data: AdminSettings }) {
  const qc = useQueryClient();
  const reauth = useReauth();
  const superAdmin = hasRole(useUser(), "SUPER_ADMIN");
  const [s, setS] = useState<RuntimeSettings>(data.settings);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<RuntimeSettings>) => setS({ ...s, ...patch });
  const num = (key: keyof RuntimeSettings) => ({
    type: "number" as const,
    value: String(s[key]),
    onChange: (e: { target: { value: string } }) => set({ [key]: Number(e.target.value) }),
  });
  const dirty = (Object.keys(s) as (keyof RuntimeSettings)[]).some((k) => s[k] !== data.settings[k]);
  const candidatesError = s.retrieval_candidates < s.retrieval_top_k ? "Must be at least the number of passages per answer." : null;
  const held = data.files_held.filter((h) => h.files > 0);

  async function save() {
    setBusy(true);
    try {
      const saved = await reauth(() => put<AdminSettings>("/admin/settings", { ...s, reason }));
      qc.setQueryData(["admin-settings"], saved);
      qc.invalidateQueries({ queryKey: ["admin-config"] });
      toast("Settings saved.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <fieldset disabled={!superAdmin} className="space-y-6">
      {!superAdmin && <Notice tone="neutral">You can view these settings. Only a super admin can change them.</Notice>}

      <Section title="File storage" description="Where an uploaded file is held, and for how long.">
        <div className="space-y-5">
          <div role="radiogroup" aria-label="Where uploaded files are stored" className="space-y-3">
            {data.storage_backends.map((b) => (
              <label key={b} className="flex items-start gap-3">
                <input
                  type="radio"
                  name="storage_backend"
                  className="mt-1 size-4 accent-[var(--action)]"
                  checked={s.storage_backend === b}
                  onChange={() => set({ storage_backend: b })}
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-ink">
                    {BACKENDS[b].label}
                    {data.defaults.storage_backend === b && <span className="ml-1.5 font-normal text-faint">default</span>}
                  </span>
                  <span className="mt-0.5 block text-[0.82rem] text-muted">{BACKENDS[b].description}</span>
                </span>
              </label>
            ))}
          </div>
          <Switch
            checked={s.keep_original_files}
            onCheckedChange={(v) => set({ keep_original_files: v })}
            disabled={!superAdmin}
            label="Keep original files after indexing"
            description="Off: an uploaded file is deleted as soon as its text is indexed. Answers, citations, research and the graph keep working from the indexed text. Users can't download the original, and a file must be uploaded again if you later change how text is split. Applies to files indexed from now on."
          />
          <p className="text-[0.82rem] text-muted">
            {held.length === 0
              ? "No original files are held right now."
              : `Held right now: ${held.map((h) => `${formatNumber(h.files)} file${h.files === 1 ? "" : "s"} (${formatBytes(h.bytes)}) on ${BACKENDS[h.backend as StorageBackend]?.label ?? h.backend}`).join(", ")}.`}{" "}
            Changing the location affects new uploads; files already held stay where they are until they are indexed or deleted.
          </p>
        </div>
      </Section>

      <Section title="Answers" description="How much of the library is read to answer one question. Plan limits still cap each user.">
        <div className="grid gap-5 sm:grid-cols-3">
          <Field label="Context size (tokens)" hint={`Most text given to the model per question. Default ${formatNumber(data.defaults.context_max_tokens)}.`}>
            {(f) => <Input {...f} {...num("context_max_tokens")} min={1000} max={200000} step={500} />}
          </Field>
          <Field label="Passages per answer" hint={`Passages kept after ranking. Default ${data.defaults.retrieval_top_k}.`}>
            {(f) => <Input {...f} {...num("retrieval_top_k")} min={1} max={50} />}
          </Field>
          <Field label="Candidates searched" hint={`Passages fetched before ranking. Default ${data.defaults.retrieval_candidates}.`} error={candidatesError}>
            {(f) => <Input {...f} {...num("retrieval_candidates")} min={5} max={400} />}
          </Field>
        </div>
      </Section>

      <Section title="AI usage">
        <div className="space-y-5">
          <Field
            label="Monthly AI budget for the whole service (USD)"
            hint={`New questions, research and graph extraction stop when this month's estimated spend reaches it. Default ${formatNumber(data.defaults.platform_monthly_ai_budget_usd)}.`}
            className="max-w-sm"
          >
            {(f) => <Input {...f} {...num("platform_monthly_ai_budget_usd")} min={0} step={10} />}
          </Field>
          <Switch
            checked={s.graph_extraction_enabled}
            onCheckedChange={(v) => set({ graph_extraction_enabled: v })}
            disabled={!superAdmin}
            label="Build the knowledge graph for new files"
            description="Each indexed file is read by the model to extract entities and relationships. Turning this off saves AI calls; existing graphs stay."
          />
        </div>
      </Section>

      <Section title="Sign-ups">
        <Switch
          checked={s.registration_enabled}
          onCheckedChange={(v) => set({ registration_enabled: v })}
          disabled={!superAdmin}
          label="Allow new accounts"
          description="Off: nobody can register, by password or with Google. Existing users can still sign in."
        />
      </Section>

      {superAdmin && (
        <div className="flex flex-wrap items-center gap-2">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for change" className="min-w-40 flex-1" aria-label="Reason" />
          <Button variant="ghost" onClick={() => setS(data.defaults)}>
            Use defaults
          </Button>
          <Button onClick={save} loading={busy} disabled={!dirty || !!candidatesError || reason.trim().length < 3}>
            Save settings
          </Button>
        </div>
      )}
      <p className="text-[0.8rem] text-muted">
        {data.updated_at ? `Last changed ${formatDateTime(data.updated_at)}.` : "Running on the defaults from the server environment."} API keys and other secrets are set in
        the server environment, not here.
      </p>
    </fieldset>
  );
}
