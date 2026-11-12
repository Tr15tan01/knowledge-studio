"use client";

import { useState, type ReactNode } from "react";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { GIB, MIB, formatBytes } from "@/lib/format";
import type { AdminPlan, EntitlementsConfig } from "@/types/admin";

export const FEATURE_LABELS: Record<string, string> = {
  semantic_search: "Semantic search",
  hybrid_search: "Hybrid keyword + semantic search",
  basic_knowledge_graph: "Knowledge graph",
  advanced_knowledge_graph: "Advanced graph filters",
  deep_research: "Deep research reports",
  cross_document_research: "Cross-document research",
  pdf_export: "PDF export",
  markdown_export: "Markdown export",
  advanced_filters: "Advanced library filters",
  saved_searches: "Saved searches",
  priority_processing: "Higher processing priority (fair use)",
};

type NumKey = Exclude<keyof EntitlementsConfig, "features">;

export const LIMIT_FIELDS: { key: NumKey; label: string; group: string; kind?: "bytes" | "usd"; hint?: string }[] = [
  { key: "upload_limit_per_period", label: "Uploads per period", group: "Quotas" },
  { key: "storage_limit_bytes", label: "Total storage", group: "Quotas", kind: "bytes", hint: "Original uploaded files." },
  { key: "max_file_size_bytes", label: "Largest file", group: "Quotas", kind: "bytes" },
  { key: "question_limit_per_period", label: "AI questions per period", group: "Quotas" },
  { key: "research_report_limit_per_period", label: "Research reports per period", group: "Quotas" },
  { key: "max_saved_research_projects", label: "Active research projects", group: "Concurrency" },
  { key: "max_concurrent_research_jobs", label: "Concurrent research runs", group: "Concurrency" },
  { key: "max_concurrent_jobs", label: "Concurrent background jobs", group: "Concurrency" },
  { key: "max_input_tokens_per_operation", label: "Max input tokens per operation", group: "Compute limits" },
  { key: "max_output_tokens_per_operation", label: "Max output tokens per operation", group: "Compute limits" },
  { key: "max_ai_calls_per_operation", label: "Max AI calls per operation", group: "Compute limits" },
  { key: "max_research_steps", label: "Max research steps", group: "Compute limits" },
  { key: "max_retrieval_iterations", label: "Max retrieval iterations", group: "Compute limits" },
  { key: "max_operation_seconds", label: "Max operation duration (seconds)", group: "Compute limits" },
  { key: "daily_compute_budget", label: "Daily compute budget (units)", group: "Compute budgets", hint: "1 unit = 1,000 weighted tokens." },
  { key: "monthly_compute_budget", label: "Monthly compute budget (units)", group: "Compute budgets" },
  { key: "max_daily_cost_usd", label: "Max daily AI spend per user (USD)", group: "Compute budgets", kind: "usd" },
  { key: "max_monthly_cost_usd", label: "Max monthly AI spend per user (USD)", group: "Compute budgets", kind: "usd" },
  { key: "max_pdf_export_pages", label: "Max PDF export pages", group: "Exports", hint: "0 disables PDF export length." },
];

export type PlanDraft = {
  name: string;
  slug: string;
  description: string;
  is_public: boolean;
  is_active: boolean;
  display_order: number;
  currency: string;
  monthly: string; // major units as typed
  annual: string;
  tagline: string;
  highlights: string;
  entitlements: EntitlementsConfig;
};

const toMajor = (minor: number | null) => (minor === null || minor === undefined ? "" : (minor / 100).toFixed(2).replace(/\.00$/, ""));
export const toMinor = (major: string): number | null => (major.trim() === "" ? null : Math.round(Number(major) * 100));

export function draftFromPlan(p: AdminPlan): PlanDraft {
  return {
    name: p.name,
    slug: p.slug,
    description: p.description,
    is_public: p.is_public,
    is_active: p.is_active,
    display_order: p.display_order,
    currency: p.pricing.currency,
    monthly: toMajor(p.pricing.monthly_display_price_minor),
    annual: toMajor(p.pricing.annual_display_price_minor),
    tagline: p.marketing?.tagline || "",
    highlights: (p.marketing?.highlights || []).join("\n"),
    entitlements: { ...p.entitlements, features: { ...p.entitlements.features } },
  };
}

export function blankDraft(base?: AdminPlan): PlanDraft {
  const d = base ? draftFromPlan(base) : null;
  return {
    name: "",
    slug: "",
    description: "",
    is_public: false,
    is_active: true,
    display_order: 100,
    currency: d?.currency || "USD",
    monthly: "",
    annual: "",
    tagline: "",
    highlights: "",
    entitlements: d?.entitlements ?? {
      upload_limit_per_period: 5,
      storage_limit_bytes: 500 * MIB,
      max_file_size_bytes: 25 * MIB,
      question_limit_per_period: 30,
      research_report_limit_per_period: 1,
      max_concurrent_research_jobs: 1,
      max_saved_research_projects: 1,
      max_concurrent_jobs: 2,
      max_input_tokens_per_operation: 24000,
      max_output_tokens_per_operation: 2048,
      max_research_steps: 8,
      max_retrieval_iterations: 6,
      max_ai_calls_per_operation: 16,
      max_operation_seconds: 600,
      daily_compute_budget: 400,
      monthly_compute_budget: 3000,
      max_daily_cost_usd: 0.5,
      max_monthly_cost_usd: 2,
      max_pdf_export_pages: 0,
      features: Object.fromEntries(Object.keys(FEATURE_LABELS).map((k) => [k, false])),
    },
  };
}

/** Byte quantity input in binary units; the stored value is always integer bytes. */
function BytesInput({ value, onChange, id, ...rest }: { value: number; onChange: (v: number) => void; id: string; "aria-describedby"?: string }) {
  const unit = value >= GIB && value % GIB === 0 ? "GiB" : "MiB";
  const [u, setU] = useState<"MiB" | "GiB">(unit);
  const mult = u === "GiB" ? GIB : MIB;
  return (
    <div className="flex gap-2">
      <Input id={id} {...rest} type="number" min={0} step="any" value={+(value / mult).toFixed(3)} onChange={(e) => onChange(Math.round(Number(e.target.value || 0) * mult))} className="flex-1" />
      <Select value={u} onChange={(e) => setU(e.target.value as "MiB" | "GiB")} className="w-24" aria-label="Unit">
        <option>MiB</option>
        <option>GiB</option>
      </Select>
    </div>
  );
}

export function PlanForm({ draft, onChange, mode, disabled }: { draft: PlanDraft; onChange: (d: PlanDraft) => void; mode: "create" | "edit"; disabled?: boolean }) {
  const set = <K extends keyof PlanDraft>(k: K, v: PlanDraft[K]) => onChange({ ...draft, [k]: v });
  const setEnt = (k: NumKey, v: number) => onChange({ ...draft, entitlements: { ...draft.entitlements, [k]: v } });
  const groups = [...new Set(LIMIT_FIELDS.map((f) => f.group))];
  return (
    <fieldset disabled={disabled} className="space-y-8">
      <Block title="Plan">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Display name">{(p) => <Input {...p} value={draft.name} maxLength={60} onChange={(e) => set("name", e.target.value)} />}</Field>
          <Field label="Slug" hint={mode === "edit" ? "Slugs can't change after creation." : "Lowercase letters, digits and hyphens."}>
            {(p) => <Input {...p} value={draft.slug} disabled={mode === "edit"} maxLength={41} onChange={(e) => set("slug", e.target.value.toLowerCase())} />}
          </Field>
        </div>
        <Field label="Description">{(p) => <Textarea {...p} rows={2} maxLength={600} value={draft.description} onChange={(e) => set("description", e.target.value)} />}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Tagline" optional>
            {(p) => <Input {...p} value={draft.tagline} maxLength={160} onChange={(e) => set("tagline", e.target.value)} />}
          </Field>
          <Field label="Order on pricing page" hint="Lower numbers appear first.">
            {(p) => <Input {...p} type="number" min={0} max={10000} value={draft.display_order} onChange={(e) => set("display_order", Number(e.target.value))} />}
          </Field>
        </div>
        <Field label="Highlights" hint="One per line. Shown on the pricing page." optional>
          {(p) => <Textarea {...p} rows={4} value={draft.highlights} onChange={(e) => set("highlights", e.target.value)} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Switch checked={draft.is_public} onCheckedChange={(v) => set("is_public", v)} label="Public" description="Shown on the pricing page and available for checkout." />
          {mode === "edit" && <Switch checked={draft.is_active} onCheckedChange={(v) => set("is_active", v)} label="Active" description="Inactive plans can't be chosen for new checkouts." />}
        </div>
      </Block>

      <Block title="Display prices" description="What the pricing page shows. The amount charged is set by the Paddle price mapped to this plan.">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Currency">{(p) => <Input {...p} value={draft.currency} maxLength={3} onChange={(e) => set("currency", e.target.value.toUpperCase())} />}</Field>
          <Field label="Monthly price">{(p) => <Input {...p} type="number" min={0} step="0.01" value={draft.monthly} onChange={(e) => set("monthly", e.target.value)} />}</Field>
          <Field label="Yearly price">{(p) => <Input {...p} type="number" min={0} step="0.01" value={draft.annual} onChange={(e) => set("annual", e.target.value)} />}</Field>
        </div>
      </Block>

      {groups.map((g) => (
        <Block key={g} title={g}>
          <div className="grid gap-4 sm:grid-cols-2">
            {LIMIT_FIELDS.filter((f) => f.group === g).map((f) => (
              <Field key={f.key} label={f.label} hint={f.kind === "bytes" ? `${formatBytes(draft.entitlements[f.key])}${f.hint ? `. ${f.hint}` : ""}` : f.hint}>
                {(p) =>
                  f.kind === "bytes" ? (
                    <BytesInput {...p} value={draft.entitlements[f.key]} onChange={(v) => setEnt(f.key, v)} />
                  ) : (
                    <Input {...p} type="number" min={0} step={f.kind === "usd" ? "0.01" : "1"} value={draft.entitlements[f.key]} onChange={(e) => setEnt(f.key, Number(e.target.value))} />
                  )
                }
              </Field>
            ))}
          </div>
        </Block>
      ))}

      <Block title="Features">
        <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
          {Object.entries(FEATURE_LABELS).map(([k, label]) => (
            <Switch
              key={k}
              checked={!!draft.entitlements.features[k]}
              onCheckedChange={(v) => onChange({ ...draft, entitlements: { ...draft.entitlements, features: { ...draft.entitlements.features, [k]: v } } })}
              label={label}
            />
          ))}
        </div>
      </Block>
    </fieldset>
  );
}

function Block({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="space-y-4">
      <div>
        <h3 className="font-semibold">{title}</h3>
        {description && <p className="text-sm text-muted">{description}</p>}
      </div>
      {children}
    </section>
  );
}

/** Field-level differences between two entitlement sets, for the review step. */
export function entitlementDiff(a: EntitlementsConfig, b: EntitlementsConfig): { label: string; from: string; to: string }[] {
  const out: { label: string; from: string; to: string }[] = [];
  for (const f of LIMIT_FIELDS) {
    if (a[f.key] !== b[f.key]) {
      const fmt = (v: number) => (f.kind === "bytes" ? formatBytes(v) : f.kind === "usd" ? `$${v}` : String(v));
      out.push({ label: f.label, from: fmt(a[f.key]), to: fmt(b[f.key]) });
    }
  }
  for (const k of Object.keys(FEATURE_LABELS)) {
    if (!!a.features[k] !== !!b.features[k]) out.push({ label: FEATURE_LABELS[k]!, from: a.features[k] ? "On" : "Off", to: b.features[k] ? "On" : "Off" });
  }
  return out;
}

export function draftToPayload(d: PlanDraft) {
  return {
    name: d.name.trim(),
    description: d.description.trim(),
    is_public: d.is_public,
    display_order: d.display_order,
    pricing: { currency: d.currency, monthly_display_price_minor: toMinor(d.monthly), annual_display_price_minor: toMinor(d.annual) },
    marketing: { tagline: d.tagline.trim(), highlights: d.highlights.split("\n").map((s) => s.trim()).filter(Boolean) },
    entitlements: d.entitlements,
  };
}
