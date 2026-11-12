"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CreditCard, ExternalLink, Loader2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Badge, ErrorState, LoadingRows, Notice, PageHeader, Section, UsageBar } from "@/components/ui/misc";
import { UsageMeters } from "@/features/account/usage-meters";
import { qk, useUsage } from "@/features/auth/session";
import { getPaddle } from "@/features/billing/paddle-checkout";
import { IntervalToggle, PlanTable, planPrice, type Interval } from "@/features/billing/plan-table";
import { ApiError, get, post } from "@/lib/api";
import { formatDate, formatDateTime, formatMoneyMinor, formatNumber } from "@/lib/format";
import type { BillingResponse, PlansResponse, PublicPlan } from "@/types/api";

const STATUS: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "neutral" }> = {
  active: { label: "Active", tone: "ok" },
  trialing: { label: "Trial", tone: "ok" },
  past_due: { label: "Payment overdue", tone: "danger" },
  paused: { label: "Paused", tone: "warn" },
  canceled: { label: "Cancelled", tone: "neutral" },
};

type Preview = { status: "preview"; currency: string; immediate_charge_minor: string | number | null; next_billed_at: string | null; plan: string; interval: Interval; message: string };

function BillingView() {
  const qc = useQueryClient();
  const router = useRouter();
  const params = useSearchParams();
  const usage = useUsage();
  const billing = useQuery({ queryKey: ["billing"], queryFn: () => get<BillingResponse>("/billing/subscription") });
  const plans = useQuery({ queryKey: ["plans"], queryFn: () => get<PlansResponse>("/plans") });
  const [interval, setBillingInterval] = useState<Interval>("month");
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ plan: PublicPlan; data: Preview } | null>(null);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [awaiting, setAwaiting] = useState(params.get("checkout") === "complete");
  const startPlan = useRef<string | null>(null);

  const b = billing.data;
  const sub = b?.subscription;
  const currentSlug = b?.entitlements.plan.slug;

  // After checkout we wait for Paddle's verified webhook; the redirect itself grants nothing.
  useEffect(() => {
    if (!awaiting) return;
    if (startPlan.current === null && b) startPlan.current = b.entitlements.plan.slug;
    const t = setInterval(() => {
      qc.invalidateQueries({ queryKey: ["billing"] });
      qc.invalidateQueries({ queryKey: qk.usage });
    }, 3000);
    const stop = setTimeout(() => setAwaiting(false), 120_000);
    return () => {
      clearInterval(t);
      clearTimeout(stop);
    };
  }, [awaiting, b, qc]);
  useEffect(() => {
    if (awaiting && b?.subscription && b.entitlements.source !== "default" && startPlan.current !== null && b.entitlements.plan.slug !== startPlan.current) {
      setAwaiting(false);
      toast.success(`You're on ${b.entitlements.plan.name}. Thanks for subscribing.`);
      router.replace("/app/billing");
    }
  }, [awaiting, b, router]);

  async function checkout(plan: PublicPlan) {
    if (!b?.billing_enabled) return;
    setBusy(plan.id);
    try {
      const res = await post<{ transaction_id: string; client_token: string; environment: "sandbox" | "production"; success_path: string }>("/billing/checkout", { plan_id: plan.id, interval });
      const paddle = await getPaddle(res.client_token, res.environment, (name) => {
        if (name === "checkout.completed") {
          startPlan.current = currentSlug ?? null;
          setAwaiting(true);
        }
      });
      if (!paddle) throw new Error("Paddle checkout couldn't load. Check your connection or ad blocker.");
      paddle.Checkout.open({ transactionId: res.transaction_id, settings: { successUrl: `${window.location.origin}${res.success_path}`, displayMode: "overlay" } });
    } catch (e) {
      toast.error(e instanceof ApiError || e instanceof Error ? e.message : "Checkout failed.");
    } finally {
      setBusy(null);
    }
  }

  async function changePlan(plan: PublicPlan, confirm: boolean) {
    setBusy(plan.id);
    try {
      const res = await post<Preview | { status: "requested"; message: string }>("/billing/change-plan", { plan_id: plan.id, interval, confirm });
      if (res.status === "preview") setPreview({ plan, data: res });
      else {
        toast(res.message);
        setPreview(null);
        startPlan.current = currentSlug ?? null;
        setAwaiting(true);
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't change plan.");
    } finally {
      setBusy(null);
    }
  }

  async function portal() {
    setBusy("portal");
    try {
      const r = await post<{ url: string }>("/billing/portal");
      window.open(r.url, "_blank", "noopener,noreferrer");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't open the billing portal.");
    } finally {
      setBusy(null);
    }
  }

  async function resume() {
    setBusy("resume");
    try {
      await post("/billing/resume");
      toast("Cancellation withdrawn. It takes effect when Paddle confirms.");
      qc.invalidateQueries({ queryKey: ["billing"] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't resume.");
    } finally {
      setBusy(null);
    }
  }

  if (billing.isError) return <ErrorState error={billing.error} onRetry={() => billing.refetch()} />;
  if (!b || !usage.data) return <LoadingRows rows={8} />;
  const st = STATUS[sub?.status || ""];
  const hasPaid = !!sub && ["active", "trialing", "past_due", "paused"].includes(sub.status);
  const compute = usage.data.usage;

  function action(p: PublicPlan) {
    const isCurrent = p.slug === currentSlug && (!sub || sub.billing_interval === interval || !p.monthly_price_minor);
    const free = !p.monthly_price_minor && !p.annual_price_minor;
    if (isCurrent) return <Button variant="secondary" className="w-full" disabled>Current plan</Button>;
    if (free) return hasPaid ? <Button variant="ghost" className="w-full" onClick={() => setCancelOpen(true)}>Downgrade to {p.name}</Button> : null;
    if (!b!.billing_enabled) return <Button variant="secondary" className="w-full" disabled>Billing disabled</Button>;
    if (!p.checkout_available[interval]) return <Button variant="secondary" className="w-full" disabled>Not available {interval === "year" ? "yearly" : "monthly"}</Button>;
    if (hasPaid)
      return (
        <Button className="w-full" onClick={() => changePlan(p, false)} loading={busy === p.id}>
          Switch to {p.name}
        </Button>
      );
    return (
      <Button className="w-full" onClick={() => checkout(p)} loading={busy === p.id}>
        Upgrade to {p.name}
      </Button>
    );
  }

  return (
    <>
      <PageHeader title="Usage & billing" description="Your plan, what you've used this period, and your subscription." />

      {!b.billing_enabled && (
        <Notice tone="neutral" className="mb-6">
          Billing is disabled on this server (development mode). Plans and quotas still apply, but no payments can be taken and no subscription can be started.
        </Notice>
      )}
      {awaiting && (
        <Notice tone="action" className="mb-6" icon={<Loader2 className="size-4 animate-spin" />}>
          Waiting for Paddle to confirm your payment. Your plan changes as soon as the confirmation arrives, usually within a few seconds.
        </Notice>
      )}
      {sub?.status === "past_due" && (
        <Notice tone="danger" className="mb-6">
          Your last payment didn&apos;t go through{sub.past_due_since ? ` (since ${formatDate(sub.past_due_since)})` : ""}. Update your payment method to keep Premium. Your files are never deleted.
        </Notice>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Section
          title="This period"
          description={`${formatDate(b.entitlements.period.start)} to ${formatDate(b.entitlements.period.end)} (UTC). ${b.entitlements.period.kind === "calendar_month" ? "Free plans reset on the 1st of each month." : "Aligned with your billing cycle."}`}
        >
          <UsageMeters usage={usage.data} />
        </Section>

        <div className="space-y-6">
          <Section title="Plan">
            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-lg font-semibold">{b.entitlements.plan.name}</span>
                {st ? <Badge tone={st.tone}>{st.label}</Badge> : <Badge>{b.entitlements.subscription_status}</Badge>}
              </div>
              {sub?.billing_interval && <p className="text-muted">Billed {sub.billing_interval === "year" ? "yearly" : "monthly"}</p>}
              {sub?.current_period_end && (
                <p>
                  {sub.cancels_at_period_end ? "Ends on" : "Renews on"} <span className="font-medium">{formatDate(sub.current_period_end)}</span>
                </p>
              )}
              {sub?.scheduled_change && !sub.cancels_at_period_end && (
                <p className="text-muted">
                  Scheduled: {sub.scheduled_change.action} {sub.scheduled_change.effective_at ? `on ${formatDate(sub.scheduled_change.effective_at)}` : ""}
                </p>
              )}
              {b.entitlements.active_grants.map((g, i) => (
                <Notice key={i} tone="highlight">
                  {g.reason || "A promotional allowance"} applies until {formatDate(g.expires_at)}.
                </Notice>
              ))}
              <div className="flex flex-wrap gap-2 pt-1">
                {b.has_billing_portal && (
                  <Button size="sm" variant="secondary" onClick={portal} loading={busy === "portal"}>
                    <CreditCard className="size-4" /> Payment details <ExternalLink className="size-3.5" />
                  </Button>
                )}
                {hasPaid && !sub!.cancels_at_period_end && (
                  <Button size="sm" variant="ghost" onClick={() => setCancelOpen(true)}>
                    Cancel subscription
                  </Button>
                )}
                {sub?.cancels_at_period_end && (
                  <Button size="sm" onClick={resume} loading={busy === "resume"}>
                    Keep my subscription
                  </Button>
                )}
              </div>
            </div>
          </Section>

          <Section title="AI compute" description="Internal fair-use budgets that keep long operations bounded. Ordinary questions rarely reach them.">
            <div className="space-y-3 text-sm">
              {(
                [
                  ["Today", compute.compute_daily],
                  ["This month", compute.compute_monthly],
                ] as const
              ).map(([l, m]) => (
                <div key={l}>
                  <div className="mb-1 flex justify-between">
                    <span>{l}</span>
                    <span className="tabular-nums text-muted">
                      {formatNumber(m.used)} of {formatNumber(m.limit)} units
                    </span>
                  </div>
                  <UsageBar used={m.used} reserved={m.reserved} limit={m.limit} label={`Compute ${l}`} />
                </div>
              ))}
            </div>
          </Section>
        </div>
      </div>

      <Section
        className="mt-6"
        title="Plans"
        description="Prices shown before tax. The amount you're charged is confirmed in Paddle's checkout."
        actions={<IntervalToggle value={interval} onChange={setBillingInterval} />}
      >
        {plans.data ? <PlanTable plans={plans.data.plans} interval={interval} currentSlug={currentSlug} renderAction={action} /> : <LoadingRows rows={6} />}
        <ul className="mt-4 space-y-1 text-[0.8rem] text-muted">
          {plans.data?.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
          <li>Downgrading never deletes files. If you&apos;re over a smaller plan&apos;s limits, new uploads pause until you&apos;re back under them.</li>
        </ul>
      </Section>

      {b.history.length > 0 && (
        <Section className="mt-6" title="Billing history">
          <ul className="-my-1 divide-y divide-rule text-sm">
            {b.history.map((h, i) => (
              <li key={i} className="flex justify-between gap-4 py-2">
                <span>{h.action.replace(/^billing\./, "").replace(/_/g, " ")}</span>
                <span className="tabular-nums text-muted">{formatDateTime(h.at)}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Dialog
        open={!!preview}
        onOpenChange={(v) => !v && setPreview(null)}
        title={`Switch to ${preview?.plan.name}`}
        description={preview?.data.message}
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setPreview(null)}>
              Keep current plan
            </Button>
            <Button onClick={() => preview && changePlan(preview.plan, true)} loading={busy === preview?.plan.id}>
              Confirm change
            </Button>
          </>
        }
      >
        {preview && (
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-muted">New price</dt>
              <dd>
                {planPrice(preview.plan, preview.data.interval).main} {planPrice(preview.plan, preview.data.interval).sub}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted">Charged now (prorated)</dt>
              <dd className="font-medium">
                {preview.data.immediate_charge_minor !== null ? formatMoneyMinor(Number(preview.data.immediate_charge_minor), preview.data.currency || "USD") : "Nothing now"}
              </dd>
            </div>
            {preview.data.next_billed_at && (
              <div className="flex justify-between">
                <dt className="text-muted">Next bill</dt>
                <dd>{formatDate(preview.data.next_billed_at)}</dd>
              </div>
            )}
          </dl>
        )}
      </Dialog>
      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        title="Cancel your subscription?"
        description="You keep Premium until the end of the current billing period, then move to the Free plan. Your files, reports and findings are never deleted."
        confirmLabel="Cancel at period end"
        danger
        onConfirm={async () => {
          const r = await post<{ message: string }>("/billing/cancel");
          toast(r.message);
          qc.invalidateQueries({ queryKey: ["billing"] });
        }}
      />
    </>
  );
}

export default function BillingPage() {
  return (
    <Suspense fallback={<LoadingRows rows={8} />}>
      <BillingView />
    </Suspense>
  );
}
