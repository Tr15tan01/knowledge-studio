"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/misc";
import { ApiError, post } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ message: string; delivery: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ message: string; email_delivery_configured: boolean }>("/auth/password/forgot", { email });
      setDone({ message: r.message, delivery: r.email_delivery_configured });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className="text-[2rem] font-semibold tracking-[-0.025em]">Reset your password</h1>
      {done ? (
        <>
          <p className="mt-3 text-muted">{done.message}</p>
          {!done.delivery && (
            <Notice tone="warn" className="mt-5">
              Email delivery isn&apos;t configured on this server, so no reset email was sent.
            </Notice>
          )}
        </>
      ) : (
        <form onSubmit={submit} className="mt-7 space-y-4">
          <p className="text-muted">Enter your account email and we&apos;ll send a link to choose a new password.</p>
          <Field label="Email">{(p) => <Input {...p} type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
          {error && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
              {error}
            </p>
          )}
          <Button type="submit" size="lg" className="w-full" loading={busy} disabled={!email}>
            Send reset link
          </Button>
        </form>
      )}
      <Link href="/sign-in" className="mt-6 inline-block text-sm font-medium text-action hover:underline">
        Back to sign in
      </Link>
    </>
  );
}
