"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { Badge, EmptyState, ErrorState, LoadingRows, Notice, PageHeader, Section } from "@/components/ui/misc";
import { useReauth } from "@/features/auth/reauth";
import { hasRole, useUser } from "@/features/auth/session";
import { ApiError, get, post, put } from "@/lib/api";
import { formatDateTime, formatMoneyMinor } from "@/lib/format";
import type { AdminPlan, PriceMapping } from "@/types/admin";

type MappingsResponse = { items: PriceMapping[]; paddle_environment: string; paddle_enabled: boolean };

function verifiedAmount(m: PriceMapping): string | null {
  const v = m.verified_price;
  return v ? formatMoneyMinor(v.unit_amount_minor, v.currency) : null;
}

function PricesView() {
  const params = useSearchParams();
  const qc = useQueryClient();
  const reauth = useReauth();
  const superAdmin = hasRole(useUser(), "SUPER_ADMIN");
  const maps = useQuery({ queryKey: ["admin-mappings"], queryFn: () => get<MappingsResponse>("/admin/price-mappings") });
  const plans = useQuery({ queryKey: ["admin-plans"], queryFn: () => get<{ items: AdminPlan[] }>("/admin/plans") });
  const [planId, setPlanId] = useState(params.get("plan") || "");
  const [interval, setBillingInterval] = useState<"month" | "year">("month");
  const [priceId, setPriceId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [deactivating, setDeactivating] = useState<PriceMapping | null>(null);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["admin-mappings"] });
    qc.invalidateQueries({ queryKey: ["admin-plans"] });
  };
  const selectedPlan = plans.data?.items.find((p) => p.id === planId);
  const display = selectedPlan ? (interval === "month" ? selectedPlan.pricing.monthly_display_price_minor : selectedPlan.pricing.annual_display_price_minor) : null;

  async function save() {
    setBusy("save");
    try {
      const r = await reauth(() => put<{ verified: boolean; warning: string | null }>("/admin/price-mappings", { plan_id: planId, interval, paddle_price_id: priceId.trim(), reason }));
      if (r.warning) toast.warning(r.warning);
      else toast.success("Mapping saved and verified with Paddle.");
      setPriceId("");
      setReason("");
      refresh();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't save the mapping.");
    } finally {
      setBusy(null);
    }
  }

  async function verify(m: PriceMapping) {
    setBusy(m.id);
    try {
      await post(`/admin/price-mappings/${m.id}/verify`);
      toast.success("Verified with Paddle.");
      refresh();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Verification failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader
        title="Paddle prices"
        description="Checkout charges the Paddle price mapped to a plan and interval, never the display price. Changing a mapping affects new checkouts only; existing subscriptions keep their price."
      />
      {maps.data && (
        <Notice tone={maps.data.paddle_enabled ? "neutral" : "warn"} className="mb-6">
          Paddle is {maps.data.paddle_enabled ? "enabled" : "disabled"} ({maps.data.paddle_environment} environment).
          {!maps.data.paddle_enabled && " Mappings can be saved but can't be verified, and checkout stays unavailable."}
        </Notice>
      )}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Section title="Current mappings">
          {maps.isError ? (
            <ErrorState error={maps.error} />
          ) : !maps.data ? (
            <LoadingRows rows={3} />
          ) : !maps.data.items.length ? (
            <EmptyState title="No prices mapped">Create a recurring price in Paddle, then map its ID to a plan here.</EmptyState>
          ) : (
            <div className="-mx-5 -my-4 overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead className="border-b border-rule text-left text-[0.8rem] text-muted">
                  <tr>
                    <th className="px-5 py-2.5 font-medium">Plan</th>
                    <th className="py-2.5 font-medium">Paddle price</th>
                    <th className="py-2.5 font-medium">Status</th>
                    <th className="py-2.5 font-medium">Updated</th>
                    <th />
                  </tr>
                </thead>
                <tbody className="divide-y divide-rule">
                  {maps.data.items.map((m) => (
                    <tr key={m.id} className={m.active ? undefined : "opacity-55"}>
                      <td className="px-5 py-3">
                        <div className="font-medium">{m.plan_name}</div>
                        <div className="text-muted">{m.interval === "month" ? "Monthly" : "Yearly"}</div>
                      </td>
                      <td className="py-3">
                        <code className="rounded bg-surface-2 px-1.5 py-0.5 text-[0.8rem]">{m.paddle_price_id}</code>
                        {verifiedAmount(m) && (
                          <div className="mt-0.5 text-muted">
                            Charges {verifiedAmount(m)}
                            {m.verified_price?.product_name ? ` for “${m.verified_price.product_name}”` : ""}
                            {m.verified_price && !m.verified_price.display_matches && <Badge tone="warn" className="ml-1.5">Differs from display price</Badge>}
                          </div>
                        )}
                      </td>
                      <td className="py-3">
                        <div className="flex flex-wrap gap-1">
                          {m.active ? <Badge tone="ok">Active</Badge> : <Badge>Inactive</Badge>}
                          {m.verified ? <Badge tone="ok">Verified</Badge> : <Badge tone="warn">Unverified</Badge>}
                          <Badge>{m.environment}</Badge>
                        </div>
                      </td>
                      <td className="py-3 text-muted">{formatDateTime(m.updated_at)}</td>
                      <td className="py-3 pr-4 text-right">
                        {superAdmin && m.active && (
                          <div className="flex justify-end gap-1">
                            {!m.verified && (
                              <Button size="sm" variant="ghost" onClick={() => verify(m)} loading={busy === m.id} disabled={!maps.data.paddle_enabled}>
                                Verify
                              </Button>
                            )}
                            <Button size="sm" variant="ghost" onClick={() => setDeactivating(m)}>
                              Deactivate
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>

        <Section title="Map a price" description="Create the product and recurring price in your Paddle dashboard first, then paste the price ID (pri_…).">
          {!superAdmin ? (
            <p className="text-sm text-muted">Only super administrators can change price mappings.</p>
          ) : (
            <div className="space-y-4">
              <Field label="Plan">
                {(f) => (
                  <Select {...f} value={planId} onChange={(e) => setPlanId(e.target.value)}>
                    <option value="">Choose a plan</option>
                    {plans.data?.items
                      .filter((p) => !p.archived_at)
                      .map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                  </Select>
                )}
              </Field>
              <Field label="Billing interval">
                {(f) => (
                  <Select {...f} value={interval} onChange={(e) => setBillingInterval(e.target.value as "month" | "year")}>
                    <option value="month">Monthly</option>
                    <option value="year">Yearly</option>
                  </Select>
                )}
              </Field>
              {selectedPlan && <p className="text-sm text-muted">Display price for this interval: {display !== null ? formatMoneyMinor(display, selectedPlan.pricing.currency) : "not set"}. Paddle verifies the actual amount, currency and interval.</p>}
              <Field label="Paddle price ID">{(f) => <Input {...f} value={priceId} onChange={(e) => setPriceId(e.target.value)} placeholder="pri_01h…" />}</Field>
              <Field label="Reason" hint="Recorded with who changed the mapping and when.">
                {(f) => <Input {...f} value={reason} onChange={(e) => setReason(e.target.value)} />}
              </Field>
              <Button className="w-full" onClick={save} loading={busy === "save"} disabled={!planId || !/^pri_[a-z0-9]{10,40}$/.test(priceId.trim()) || reason.trim().length < 3}>
                Save mapping
              </Button>
            </div>
          )}
        </Section>
      </div>
      <ConfirmDialog
        open={!!deactivating}
        onOpenChange={(v) => !v && setDeactivating(null)}
        title="Deactivate this mapping?"
        description="New checkouts for this plan and interval stop working until another price is mapped. Existing subscriptions aren't affected."
        confirmLabel="Deactivate"
        danger
        requireReason
        onConfirm={async (reason) => {
          await reauth(() => post(`/admin/price-mappings/${deactivating!.id}/deactivate`, { reason }));
          refresh();
        }}
      />
    </>
  );
}

export default function AdminPricesPage() {
  return (
    <Suspense fallback={<LoadingRows rows={4} />}>
      <PricesView />
    </Suspense>
  );
}
