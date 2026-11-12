"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { Notice, PageHeader, Section } from "@/components/ui/misc";
import { PlanForm, blankDraft, draftToPayload, type PlanDraft } from "@/features/admin/plan-form";
import { useReauth } from "@/features/auth/reauth";
import { hasRole, useUser } from "@/features/auth/session";
import { ApiError, get, post } from "@/lib/api";
import type { AdminPlan } from "@/types/admin";

export default function NewPlanPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const reauth = useReauth();
  const superAdmin = hasRole(useUser(), "SUPER_ADMIN");
  const plans = useQuery({ queryKey: ["admin-plans"], queryFn: () => get<{ items: AdminPlan[] }>("/admin/plans") });
  const [draft, setDraft] = useState<PlanDraft>(() => blankDraft());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setErr(null);
    try {
      const p = await reauth(() => post<AdminPlan>("/admin/plans", { ...draftToPayload(draft), slug: draft.slug }));
      toast(`${p.name} created as a hidden plan.`);
      qc.invalidateQueries({ queryKey: ["admin-plans"] });
      router.push(`/admin/plans/${p.id}`);
    } catch (e) {
      const d = e instanceof ApiError ? (e.details.errors as { loc: string[]; msg: string }[] | undefined) : undefined;
      setErr(d?.length ? d.map((x) => `${x.loc.slice(1).join(".")}: ${x.msg}`).join("; ") : e instanceof ApiError ? e.message : "Couldn't create the plan.");
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
        title="New plan"
        description="New plans start as version 1. Keep them hidden until a Paddle price is mapped and you've previewed the pricing page."
        actions={
          <Button onClick={create} loading={busy} disabled={!superAdmin || draft.name.trim().length < 2 || !draft.slug}>
            Create plan
          </Button>
        }
      />
      {!superAdmin && <Notice tone="warn" className="mb-5">Only super administrators can create plans.</Notice>}
      {err && <Notice tone="danger" className="mb-5">{err}</Notice>}
      <Section>
        <div className="mb-6 flex flex-wrap items-center gap-3 text-sm">
          <span className="text-muted">Start from the limits of</span>
          <Select
            className="w-auto"
            aria-label="Copy limits from plan"
            onChange={(e) => {
              const base = plans.data?.items.find((p) => p.id === e.target.value);
              if (base) setDraft((d) => ({ ...blankDraft(base), name: d.name, slug: d.slug, description: d.description }));
            }}
            defaultValue=""
          >
            <option value="">Defaults</option>
            {plans.data?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </div>
        <PlanForm draft={draft} onChange={setDraft} mode="create" disabled={!superAdmin} />
      </Section>
    </>
  );
}
