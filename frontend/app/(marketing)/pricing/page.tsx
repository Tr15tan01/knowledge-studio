"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ButtonLink } from "@/components/ui/button";
import { ErrorState, LoadingRows, Notice } from "@/components/ui/misc";
import { useSession } from "@/features/auth/session";
import { IntervalToggle, PlanTable, type Interval } from "@/features/billing/plan-table";
import { get } from "@/lib/api";
import type { PlansResponse } from "@/types/api";

export default function PricingPage() {
  const [interval, setBillingInterval] = useState<Interval>("month");
  const plans = useQuery({ queryKey: ["plans"], queryFn: () => get<PlansResponse>("/plans") });
  const { data: session } = useSession();
  const signedIn = session?.authenticated;

  return (
    <div className="mx-auto max-w-[1000px] px-4 pt-14 sm:px-6">
      <h1 className="text-[2.6rem] font-semibold leading-tight tracking-[-0.03em]">Pricing</h1>
      <p className="mt-3 max-w-2xl text-[1.05rem] text-ink-2">
        Start free. Upgrade when your library outgrows it. Every plan includes cited answers, hybrid search and a private library.
      </p>
      <div className="mt-8 flex flex-wrap items-center justify-between gap-4">
        <IntervalToggle value={interval} onChange={setBillingInterval} />
        {plans.data && !plans.data.billing_enabled && <Notice tone="neutral">Payments aren&apos;t enabled on this server yet.</Notice>}
      </div>
      <div className="mt-6 rounded-[var(--radius-panel)] border border-rule bg-surface p-2 sm:p-4">
        {plans.isError ? (
          <ErrorState error={plans.error} onRetry={() => plans.refetch()} />
        ) : !plans.data ? (
          <LoadingRows rows={8} />
        ) : (
          <PlanTable
            plans={plans.data.plans}
            interval={interval}
            renderAction={(p) => {
              const free = !p.monthly_price_minor && !p.annual_price_minor;
              if (signedIn) return <ButtonLink href="/app/billing" variant={free ? "secondary" : "primary"} className="w-full">{free ? "Your library" : `Choose ${p.name}`}</ButtonLink>;
              return (
                <ButtonLink href={free ? "/register" : `/register?next=${encodeURIComponent("/app/billing")}`} variant={free ? "secondary" : "primary"} className="w-full">
                  {free ? "Start free" : `Start with ${p.name}`}
                </ButtonLink>
              );
            }}
          />
        )}
      </div>

      <div className="mt-12 grid gap-10 md:grid-cols-2">
        <section>
          <h2 className="text-lg font-semibold">How limits work</h2>
          <ul className="mt-3 space-y-2.5 text-[0.95rem] text-ink-2">
            <li><strong className="font-medium text-ink">Uploads</strong> count files we accept. Failed uploads don&apos;t count, and deleting a file frees storage but not an upload.</li>
            <li><strong className="font-medium text-ink">Storage</strong> is the size of your original files. Extracted text and indexes don&apos;t count against it.</li>
            <li><strong className="font-medium text-ink">Questions</strong> are counted once per question, however many steps answering it takes. A question that fails before an answer is generated isn&apos;t counted.</li>
            <li><strong className="font-medium text-ink">Research reports</strong> have their own allowance. Each run also has fair-use limits on steps, AI calls and time.</li>
          </ul>
        </section>
        <section>
          <h2 className="text-lg font-semibold">Billing</h2>
          <ul className="mt-3 space-y-2.5 text-[0.95rem] text-ink-2">
            <li>Payments are handled by Paddle, our merchant of record. Taxes are calculated at checkout.</li>
            <li>Paid plans renew automatically. Cancel any time and keep access until the end of the period.</li>
            <li>Downgrading never deletes your files. If you&apos;re over a smaller plan&apos;s limits, new uploads pause until you&apos;re back under them.</li>
            <li>Processing times aren&apos;t guaranteed, and we don&apos;t offer unlimited AI usage on any plan.</li>
          </ul>
        </section>
      </div>
    </div>
  );
}
