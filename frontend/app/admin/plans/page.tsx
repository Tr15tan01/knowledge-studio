"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Eye, MoreHorizontal, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Button, ButtonLink, IconButton } from "@/components/ui/button";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Select } from "@/components/ui/field";
import { Menu, MenuItem } from "@/components/ui/menu";
import { Badge, ErrorState, LoadingRows, Notice, PageHeader, Section } from "@/components/ui/misc";
import { useReauth } from "@/features/auth/reauth";
import { hasRole, useUser } from "@/features/auth/session";
import { IntervalToggle, PlanTable, type Interval } from "@/features/billing/plan-table";
import { ApiError, get, post } from "@/lib/api";
import { formatBytes, formatMoneyMinor, formatNumber } from "@/lib/format";
import type { AdminPlan } from "@/types/admin";
import type { PublicPlan } from "@/types/api";

export default function AdminPlansPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const reauth = useReauth();
  const superAdmin = hasRole(useUser(), "SUPER_ADMIN");
  const plans = useQuery({ queryKey: ["admin-plans"], queryFn: () => get<{ items: AdminPlan[] }>("/admin/plans") });
  const [archiving, setArchiving] = useState<AdminPlan | null>(null);
  const [defaulting, setDefaulting] = useState<AdminPlan | null>(null);
  const [preview, setPreview] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin-plans"] });

  const items = [...(plans.data?.items ?? [])].sort((a, b) => Number(!!a.archived_at) - Number(!!b.archived_at) || a.display_order - b.display_order);
  const live = items.filter((p) => !p.archived_at);

  async function move(p: AdminPlan, dir: -1 | 1) {
    const order = live.map((x) => x.id);
    const i = order.indexOf(p.id);
    const j = i + dir;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j]!, order[i]!];
    try {
      await post("/admin/plans/reorder", { order });
      refresh();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't reorder.");
    }
  }

  return (
    <>
      <PageHeader
        title="Plans & pricing"
        description="Every limit, feature and display price comes from this catalog. Edits create a new version; you choose who it applies to."
        actions={
          <>
            <Button variant="secondary" onClick={() => setPreview(true)}>
              <Eye className="size-4" /> Preview pricing page
            </Button>
            {superAdmin && (
              <ButtonLink href="/admin/plans/new">
                <Plus className="size-4" /> New plan
              </ButtonLink>
            )}
          </>
        }
      />
      {!superAdmin && (
        <Notice tone="neutral" className="mb-5">
          You can view plans. Creating and editing plans, prices and defaults requires a super administrator.
        </Notice>
      )}
      {plans.isError ? (
        <ErrorState error={plans.error} onRetry={() => plans.refetch()} />
      ) : !plans.data ? (
        <LoadingRows rows={4} />
      ) : (
        <Section>
          <div className="-mx-5 -my-4 overflow-x-auto">
            <table className="w-full min-w-[820px] text-sm">
              <thead className="border-b border-rule text-left text-[0.8rem] text-muted">
                <tr>
                  <th className="px-5 py-2.5 font-medium">Plan</th>
                  <th className="py-2.5 font-medium">Display price</th>
                  <th className="py-2.5 font-medium">Key limits</th>
                  <th className="py-2.5 font-medium">Subscribers by version</th>
                  <th className="py-2.5 font-medium">Paddle</th>
                  <th className="w-28" />
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {items.map((p) => {
                  const subs = Object.values(p.subscribers_by_version).reduce((a, b) => a + b, 0);
                  const liveIdx = live.indexOf(p);
                  return (
                    <tr key={p.id} className={p.archived_at ? "opacity-60" : undefined}>
                      <td className="px-5 py-3 align-top">
                        <Link href={`/admin/plans/${p.id}`} className="font-medium hover:underline">
                          {p.name}
                        </Link>
                        <div className="mt-1 flex flex-wrap gap-1">
                          <Badge>v{p.current_version}</Badge>
                          {p.is_default && <Badge tone="action">Default</Badge>}
                          {p.archived_at ? <Badge tone="warn">Archived</Badge> : p.is_public ? <Badge tone="ok">Public</Badge> : <Badge>Hidden</Badge>}
                          {!p.is_active && !p.archived_at && <Badge tone="warn">Inactive</Badge>}
                        </div>
                      </td>
                      <td className="py-3 align-top tabular-nums">
                        {formatMoneyMinor(p.pricing.monthly_display_price_minor, p.pricing.currency)}/mo
                        <div className="text-muted">{p.pricing.annual_display_price_minor !== null ? `${formatMoneyMinor(p.pricing.annual_display_price_minor, p.pricing.currency)}/yr` : "No yearly"}</div>
                      </td>
                      <td className="py-3 align-top text-ink-2">
                        {formatNumber(p.entitlements.upload_limit_per_period)} uploads, {formatBytes(p.entitlements.storage_limit_bytes)}
                        <div className="text-muted">
                          {formatNumber(p.entitlements.question_limit_per_period)} questions, {formatNumber(p.entitlements.research_report_limit_per_period)} reports
                        </div>
                      </td>
                      <td className="py-3 align-top tabular-nums">
                        {subs === 0 ? (
                          <span className="text-muted">None</span>
                        ) : (
                          Object.entries(p.subscribers_by_version).map(([v, n]) => (
                            <div key={v}>
                              v{v}: {formatNumber(n)}
                            </div>
                          ))
                        )}
                      </td>
                      <td className="py-3 align-top">
                        {p.price_mappings.filter((m) => m.active).length ? (
                          p.price_mappings
                            .filter((m) => m.active)
                            .map((m) => (
                              <div key={m.id} className="flex items-center gap-1.5">
                                {m.interval === "month" ? "Monthly" : "Yearly"} {m.verified ? <Badge tone="ok">Verified</Badge> : <Badge tone="warn">Unverified</Badge>}
                              </div>
                            ))
                        ) : (
                          <span className="text-muted">{p.pricing.monthly_display_price_minor ? "Not mapped" : "Free"}</span>
                        )}
                      </td>
                      <td className="py-3 pr-4 text-right align-top">
                        {superAdmin && !p.archived_at && (
                          <div className="flex items-center justify-end">
                            <IconButton label={`Move ${p.name} up`} disabled={liveIdx <= 0} onClick={() => move(p, -1)}>
                              <ArrowUp className="size-4" />
                            </IconButton>
                            <IconButton label={`Move ${p.name} down`} disabled={liveIdx >= live.length - 1} onClick={() => move(p, 1)}>
                              <ArrowDown className="size-4" />
                            </IconButton>
                            <Menu
                              trigger={
                                <IconButton label={`More actions for ${p.name}`}>
                                  <MoreHorizontal className="size-4" />
                                </IconButton>
                              }
                            >
                              <MenuItem onSelect={() => router.push(`/admin/plans/${p.id}`)}>Edit plan</MenuItem>
                              <MenuItem onSelect={() => router.push(`/admin/prices?plan=${p.id}`)}>Map Paddle prices</MenuItem>
                              {!p.is_default && <MenuItem onSelect={() => setDefaulting(p)}>Make default for new accounts</MenuItem>}
                              {!p.is_default && (
                                <MenuItem danger onSelect={() => setArchiving(p)}>
                                  Archive plan
                                </MenuItem>
                              )}
                            </Menu>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {archiving && <ArchiveDialog plan={archiving} plans={live.filter((p) => p.id !== archiving.id)} onClose={() => setArchiving(null)} onDone={refresh} />}
      <ConfirmDialog
        open={!!defaulting}
        onOpenChange={(v) => !v && setDefaulting(null)}
        title={`Make ${defaulting?.name} the default plan?`}
        description="New accounts and accounts without a paid subscription get this plan's entitlements."
        confirmLabel="Make default"
        onConfirm={async () => {
          await reauth(() => post(`/admin/plans/${defaulting!.id}/default`));
          toast("Default plan updated.");
          refresh();
        }}
      />
      <PricingPreview open={preview} onOpenChange={setPreview} />
    </>
  );
}

function ArchiveDialog({ plan, plans, onClose, onDone }: { plan: AdminPlan; plans: AdminPlan[]; onClose: () => void; onDone: () => void }) {
  const reauth = useReauth();
  const subs = Object.values(plan.subscribers_by_version).reduce((a, b) => a + b, 0);
  const [grandfather, setGrandfather] = useState(true);
  const [target, setTarget] = useState("");
  return (
    <ConfirmDialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={`Archive ${plan.name}?`}
      description="Archived plans disappear from the pricing page and can't be chosen at checkout."
      confirmLabel="Archive plan"
      danger
      requireReason
      onConfirm={async (reason) => {
        if (subs && !grandfather && !target) throw new Error("Choose to keep current subscribers or pick a plan to move them to.");
        await reauth(() => post(`/admin/plans/${plan.id}/archive`, { reason, grandfather: subs ? grandfather : false, migrate_to_plan_id: !grandfather && target ? target : null }));
        toast(`${plan.name} archived.`);
        onDone();
      }}
    >
      {subs > 0 ? (
        <div className="mb-3 space-y-3 text-sm">
          <Notice tone="warn">{formatNumber(subs)} account(s) currently use this plan.</Notice>
          <Checkbox checked={grandfather} onChange={(e) => setGrandfather(e.target.checked)} label="Keep current subscribers on this plan (grandfather)" />
          {!grandfather && (
            <Field label="Move non-paying assignments to" hint="Paid Paddle subscriptions are never repriced automatically; move them in Paddle.">
              {(p) => (
                <Select {...p} value={target} onChange={(e) => setTarget(e.target.value)}>
                  <option value="">Choose a plan</option>
                  {plans.map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          )}
        </div>
      ) : (
        <p className="mb-3 text-sm text-muted">No accounts use this plan.</p>
      )}
    </ConfirmDialog>
  );
}

function PricingPreview({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [hidden, setHidden] = useState(false);
  const [interval, setBillingInterval] = useState<Interval>("month");
  const q = useQuery({ queryKey: ["admin-plans-preview", hidden], queryFn: () => get<{ plans: (PublicPlan & { is_public: boolean })[] }>("/admin/plans-preview", { include_hidden: hidden }), enabled: open });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Pricing page preview" description="Exactly what visitors see, from the current plan versions." size="xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <IntervalToggle value={interval} onChange={setBillingInterval} />
        <Checkbox checked={hidden} onChange={(e) => setHidden(e.target.checked)} label="Include hidden plans" />
      </div>
      {q.data ? <PlanTable plans={q.data.plans} interval={interval} renderAction={(p) => <Button className="w-full" variant={p.is_public === false ? "secondary" : "primary"} disabled>{p.is_public === false ? "Hidden" : `Choose ${p.name}`}</Button>} /> : <LoadingRows rows={6} />}
    </Dialog>
  );
}
