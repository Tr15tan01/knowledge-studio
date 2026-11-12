"use client";

import { useAuthConfig } from "./session";

/** Rendered only when the backend reports GOOGLE_AUTH_ENABLED=1. */
export function GoogleButton({ next = "/app", label = "Continue with Google" }: { next?: string; label?: string }) {
  const { data } = useAuthConfig();
  if (!data?.google_enabled) return null;
  return (
    <>
      <a
        href={`/api/v1/auth/google/start?next=${encodeURIComponent(next)}`}
        className="flex h-11 w-full items-center justify-center gap-2.5 rounded-[var(--radius-control)] border border-rule-strong bg-surface text-[0.95rem] font-medium hover:bg-surface-2"
      >
        <svg viewBox="0 0 24 24" className="size-4.5" aria-hidden>
          <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.4h6.5a5.6 5.6 0 0 1-2.4 3.7v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8z" />
          <path fill="#34A853" d="M12 24c3.2 0 6-1.1 7.9-2.9l-3.9-3c-1.1.7-2.4 1.2-4 1.2-3.1 0-5.7-2.1-6.6-4.9h-4v3.1A12 12 0 0 0 12 24z" />
          <path fill="#FBBC05" d="M5.4 14.4a7.2 7.2 0 0 1 0-4.7V6.6h-4a12 12 0 0 0 0 10.8z" />
          <path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4A12 12 0 0 0 1.4 6.6l4 3.1C6.3 6.9 8.9 4.8 12 4.8z" />
        </svg>
        {label}
      </a>
      <div className="my-5 flex items-center gap-3 text-xs text-faint">
        <span className="h-px flex-1 bg-rule" />
        or use email
        <span className="h-px flex-1 bg-rule" />
      </div>
    </>
  );
}

export const OAUTH_ERRORS: Record<string, string> = {
  google_cancelled: "Google sign-in was cancelled.",
  google_email_unverified: "Your Google account's email isn't verified, so it can't be used to sign in.",
  account_exists_link_from_settings:
    "An account with this email already exists. Sign in with your password, then link Google from Settings → Security.",
  account_unavailable: "This account is unavailable. Contact support if you think this is a mistake.",
  registration_closed: "New sign-ups are closed at the moment.",
  oauth_state_invalid: "The sign-in attempt expired or was already used. Please try again.",
  oauth_expired: "The Google sign-in took too long. Please try again.",
  oauth_exchange_failed: "Google sign-in couldn't be completed. Please try again.",
  oauth_invalid_token: "Google returned an invalid response. Please try again.",
  oauth_bad_audience: "Google sign-in is misconfigured on this server.",
  oauth_bad_issuer: "Google sign-in is misconfigured on this server.",
  oauth_bad_nonce: "The sign-in attempt couldn't be verified. Please try again.",
  oauth_no_subject: "Google returned an incomplete profile. Please try again.",
};
