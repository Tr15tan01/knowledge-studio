"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, History } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Badge, ErrorState, LoadingRows, Notice, PageHeader, Section } from "@/components/ui/misc";
import { PlanForm, draftFromPlan, draftToPayload, entitlementDiff, type PlanDraft } from "@/features/admin/plan-form";
import { useReauth } from "@/features/auth/reauth";
import { hasRole, useUser } from "@/features/auth/session";
import { ApiError, get, patch, post } from "@/lib/api";
import { formatDateTime, formatMoneyMinor, formatNumber } from "@/lib/format";
import type { AdminPlan, PlanVersion } from "@/types/admin";

const POLICY: Record<string, string> = {
  new_subscribers_only: "New subscribers only. Existing subscribers keep their current version.",
  all_subscribers: "Everyone on this plan, immediately.",
  cohort: "Only the accounts you list.",
};

export default function AdminPlanEditPage() {
  const { id } = useParams<{ id: string }>();
  const plan = useQuery({ queryKey: ["admin-plans"], queryFn: () => get<{ items: AdminPlan[] }>("/admin/plans"), select: (d) => d.items.find((p) => p.id === id) });
  if (plan.isError) return <ErrorState error={plan.error} />;
  if (!plan.data) return <LoadingRows rows={8} />;
  // Keyed by version: publishing a new version starts a fresh draft from the saved plan.
  return <PlanEditor key={`${plan.data.id}:${plan.data.current_version}`} plan={plan.data} />;
}

function PlanEditor({ plan: p }: { plan: AdminPlan }) {
  const id = p.id;
  const qc = useQueryClient();
  const reauth = useReauth();
  const superAdmin = hasRole(useUser(), "SUPER_ADMIN");
  const history = useQuery({ queryKey: ["admin-plan-history", id], queryFn: () => get<{ items: PlanVersion[] }>(`/admin/plans/${id}/history`) });
  const [draft, setDraft] = useState<PlanDraft>(() => draftFromPlan(p));
  const [review, setReview] = useState(false);
  const [note, setNote] = useState("");
  const [policy, setPolicy] = useState("new_subscribers_only");
  const [cohort, setCohort] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [migrate, setMigrate] = useState(false);

  const changes = useMemo(() => {
    const original = draftFromPlan(p);
    const ent = entitlementDiff(original.entitlements, draft.entitlements);
    const price =
      original.monthly !== draft.monthly || original.annual !== draft.annual || original.currency !== draft.currency
        ? [{ label: "Display prices", from: `${original.monthly || "—"}/mo, ${original.annual || "—"}/yr ${original.currency}`, to: `${draft.monthly || "—"}/mo, ${draft.annual || "—"}/yr ${draft.currency}` }]
        : [];
    const other = JSON.stringify({ ...original, entitlements: 0, monthly: 0, annual: 0, currency: 0 }) !== JSON.stringify({ ...draft, entitlements: 0, monthly: 0, annual: 0, currency: 0 });
    return { ent, price, other, significant: ent.length + price.length > 0 };
  }, [p, draft]);

  const subs = Object.values(p.subscribers_by_version).reduce((a, b) => a + b, 0);
  const dirty = changes.significant || changes.other;

  async function publish() {
    setBusy(true);
    setErr(null);
    const full = draftToPayload(draft);
    const body: Record<string, unknown> = {
      name: full.name,
      description: full.description,
      is_public: full.is_public,
      is_active: draft.is_active,
      display_order: full.display_order,
      marketing: full.marketing,
      change_note: note.trim(),
      apply_policy: policy,
      cohort_account_ids: policy === "cohort" ? cohort.split(/[\s,]+/).filter(Boolean) : [],
      confirm: changes.significant,
    };
    if (changes.ent.length) body.entitlements = full.entitlements;
    if (changes.price.length) body.pricing = full.pricing;
    try {
      const updated = await reauth(() => patch<AdminPlan>(`/admin/plans/${id}`, body));
      toast(`Published version ${updated.current_version}.`);
      setReview(false);
      setNote("");
      setDraft(draftFromPlan(updated));
      qc.invalidateQueries({ queryKey: ["admin-plans"] });
      qc.invalidateQueries({ queryKey: ["admin-plan-history", id] });
      qc.invalidateQueries({ queryKey: ["plans"] });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Couldn't publish.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        back={
          <Link href="/admin/plans" className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
            <ArrowLeft className="size-4" /> Plans
          </Link>
        }
        title={p.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge>Version {p.current_version}</Badge>
            {p.is_default && <Badge tone="action">Default</Badge>}
            {p.archived_at && <Badge tone="warn">Archived</Badge>}
            <span>{formatNumber(subs)} accounts on this plan</span>
          </span>
        }
        actions={
          superAdmin &&
          !p.archived_at && (
            <>
              <Button variant="secondary" onClick={() => setMigrate(true)}>
                Migrate subscribers
              </Button>
              <Button variant="ghost" onClick={() => setDraft(draftFromPlan(p))} disabled={!dirty}>
                Discard changes
              </Button>
              <Button onClick={() => setReview(true)} disabled={!dirty}>
                Review and publish
              </Button>
            </>
          )
        }
      />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Section>
          <PlanForm draft={draft} onChange={setDraft} mode="edit" disabled={!superAdmin || !!p.archived_at} />
        </Section>
        <Section title={<span className="inline-flex items-center gap-2"><History className="size-4" /> Version history</span>}>
          {!history.data ? (
            <LoadingRows rows={3} />
          ) : (
            <ol className="-my-1 space-y-4">
              {history.data.items.map((v) => (
                <li key={v.version} className="text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">Version {v.version}</span>
                    <span className="tabular-nums text-faint">{formatNumber(p.subscribers_by_version[String(v.version)] || 0)} on it</span>
                  </div>
                  <div className="text-ink-2">{v.change_note}</div>
                  <div className="text-[0.78rem] text-muted">
                    {formatDateTime(v.created_at)}, {v.apply_policy?.replace(/_/g, " ")}
                  </div>
                  <div className="text-[0.78rem] text-muted">
                    {formatMoneyMinor(v.pricing.monthly_display_price_minor, v.pricing.currency)}/mo, {formatNumber(v.entitlements.question_limit_per_period)} questions
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Section>
      </div>

      <Dialog
        open={review}
        onOpenChange={setReview}
        title="Review changes"
        description="Publishing creates a new immutable version. Paddle prices and existing paid subscriptions are not changed here."
        size="lg"
        footer={
          <>
            <Button variant="ghost" onClick={() => setReview(false)}>
              Keep editing
            </Button>
            <Button onClick={publish} loading={busy} disabled={note.trim().length < 3}>
              Publish version {p.current_version + 1}
            </Button>
          </>
        }
      >
        <div className="space-y-5">
          {[...changes.price, ...changes.ent].length > 0 ? (
            <table className="w-full text-sm">
              <thead className="text-left text-muted">
                <tr>
                  <th className="py-1 font-medium">Setting</th>
                  <th className="py-1 font-medium">Now</th>
                  <th className="py-1 font-medium">New</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {[...changes.price, ...changes.ent].map((c) => (
                  <tr key={c.label}>
                    <td className="py-1.5">{c.label}</td>
                    <td className="py-1.5 text-muted">{c.from}</td>
                    <td className="py-1.5 font-medium">{c.to}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-sm text-muted">Only descriptive details changed. Quotas and prices stay the same.</p>
          )}
          {changes.price.length > 0 && <Notice tone="warn">Display prices only change what the pricing page shows. Map a matching Paddle price before checkout uses the new amount.</Notice>}
          {changes.significant && (
            <Field label="Apply to">
              {(f) => (
                <Select {...f} value={policy} onChange={(e) => setPolicy(e.target.value)}>
                  {Object.entries(POLICY).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
          {policy === "cohort" && changes.significant && (
            <Field label="Account IDs" hint="Separate with spaces, commas or new lines.">
              {(f) => <Textarea {...f} rows={3} value={cohort} onChange={(e) => setCohort(e.target.value)} />}
            </Field>
          )}
          {changes.significant && policy === "all_subscribers" && subs > 0 && (
            <Notice tone="danger">This changes the entitlements of {formatNumber(subs)} existing account(s) immediately.</Notice>
          )}
          <Field label="Change note" hint="Recorded in the version history and audit log.">
            {(f) => <Input {...f} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />}
          </Field>
          {err && <p className="text-sm text-danger">{err}</p>}
        </div>
      </Dialog>
      {migrate && <MigrateDialog plan={p} versions={history.data?.items ?? []} onClose={() => setMigrate(false)} />}
    </>
  );
}

function MigrateDialog({ plan, versions, onClose }: { plan: AdminPlan; versions: PlanVersion[]; onClose: () => void }) {
  const qc = useQueryClient();
  const reauth = useReauth();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState(String(plan.current_version));
  const [ids, setIds] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    try {
      const r = await reauth(() =>
        post<{ migrated: number }>(`/admin/plans/${plan.id}/migrate`, {
          from_version: from ? Number(from) : null,
          to_version: Number(to),
          account_ids: ids.trim() ? ids.split(/[\s,]+/).filter(Boolean) : null,
          reason,
        }),
      );
      toast(`Moved ${r.migrated} subscription(s) to version ${to}.`);
      qc.invalidateQueries({ queryKey: ["admin-plans"] });
      onClose();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Migration failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title="Migrate subscribers between versions"
      description="Moves entitlement snapshots only. Paddle prices aren't changed, so nobody is charged differently."
      footer={
        <Button onClick={go} loading={busy} disabled={reason.trim().length < 3}>
          Migrate
        </Button>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="From version" optional>
            {(f) => (
              <Select {...f} value={from} onChange={(e) => setFrom(e.target.value)}>
                <option value="">Any older version</option>
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    Version {v.version} ({plan.subscribers_by_version[String(v.version)] || 0})
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="To version">
            {(f) => (
              <Select {...f} value={to} onChange={(e) => setTo(e.target.value)}>
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    Version {v.version}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <Field label="Only these accounts" optional hint="Leave empty for everyone on the source version.">
          {(f) => <Textarea {...f} rows={2} value={ids} onChange={(e) => setIds(e.target.value)} />}
        </Field>
        <Field label="Reason">{(f) => <Input {...f} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
      </div>
    </Dialog>
  );
}
