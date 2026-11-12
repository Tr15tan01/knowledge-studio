"use client";

import { AlertTriangle, MailWarning } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Notice } from "@/components/ui/misc";
import { useAuthConfig, useUsage, useUser } from "@/features/auth/session";
import { ApiError, post } from "@/lib/api";

const NOTICE_TEXT: Record<string, string> = {
  payment_past_due: "Your last payment didn't go through. Premium stays active for a short grace period; update your payment method to keep it.",
  cancels_at_period_end: "Your subscription is set to end at the close of this billing period. Your files stay either way.",
};

/** Account-level notices: unverified email, storage thresholds, billing problems. */
export function AccountBanner() {
  const user = useUser();
  const { data: cfg } = useAuthConfig();
  const { data: usage } = useUsage();
  const [sending, setSending] = useState(false);
  const notices: React.ReactNode[] = [];

  if (user && !user.email_verified && cfg?.email_verification_required) {
    notices.push(
      <Notice key="verify" tone="warn" icon={<MailWarning className="size-4" />}>
        Verify your email to use the library.{" "}
        <button
          className="font-medium underline"
          disabled={sending}
          onClick={async () => {
            setSending(true);
            try {
              const r = await post<{ status: string; email_delivery_configured: boolean }>("/auth/resend-verification");
              toast(r.status === "sent" ? "Verification email sent." : "Email delivery isn't configured on this server.");
            } catch (e) {
              toast.error(e instanceof ApiError ? e.message : "Couldn't send the email.");
            } finally {
              setSending(false);
            }
          }}
        >
          Resend the link
        </button>
      </Notice>,
    );
  }

  const s = usage?.storage;
  if (s?.over_limit) {
    notices.push(
      <Notice key="over" tone="danger" icon={<AlertTriangle className="size-4" />}>
        Your library uses {s.display.used} of {s.display.limit}. New uploads are paused until you{" "}
        <Link href="/app/library?sort=size" className="font-medium underline">
          remove files
        </Link>{" "}
        or{" "}
        <Link href="/app/billing" className="font-medium underline">
          change plan
        </Link>
        . Nothing is deleted automatically.
      </Notice>,
    );
  } else if (s?.warning_level && s.warning_level >= 80) {
    notices.push(
      <Notice key="warn" tone="warn" icon={<AlertTriangle className="size-4" />}>
        Storage is {Math.floor(s.percent_used)}% full ({s.display.used} of {s.display.limit}).{" "}
        <Link href="/app/billing" className="font-medium underline">
          See usage
        </Link>
      </Notice>,
    );
  }
  for (const n of usage?.entitlements.notices || []) {
    const text = NOTICE_TEXT[n];
    if (!text) continue;
    notices.push(
      <Notice key={n} tone={n === "payment_past_due" ? "danger" : "neutral"} icon={<AlertTriangle className="size-4" />}>
        {text}{" "}
        <Link href="/app/billing" className="font-medium underline">
          Usage & billing
        </Link>
      </Notice>,
    );
  }
  if (!notices.length) return null;
  return <div className="mb-6 space-y-2">{notices}</div>;
}
