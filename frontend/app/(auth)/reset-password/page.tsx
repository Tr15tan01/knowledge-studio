"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { useAuthConfig } from "@/features/auth/session";
import { ApiError, post } from "@/lib/api";

function ResetForm() {
  const token = useSearchParams().get("token") || "";
  const router = useRouter();
  const min = useAuthConfig().data?.password_min_length ?? 10;
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) return setError("The two passwords don't match.");
    setBusy(true);
    setError(null);
    try {
      await post("/auth/password/reset", { token, password });
      router.replace("/sign-in?reset=1");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Reset failed.");
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <>
        <h1 className="text-[2rem] font-semibold tracking-[-0.025em]">Link incomplete</h1>
        <p className="mt-3 text-muted">This reset link is missing its token. Request a new one.</p>
        <Link href="/forgot-password" className="mt-6 inline-block font-medium text-action hover:underline">
          Request a new link
        </Link>
      </>
    );
  }
  return (
    <>
      <h1 className="text-[2rem] font-semibold tracking-[-0.025em]">Choose a new password</h1>
      <p className="mt-1.5 text-muted">Signing in with the new password ends your other sessions.</p>
      <form onSubmit={submit} className="mt-7 space-y-4">
        <Field label="New password" hint={`At least ${min} characters.`}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" minLength={min} required value={password} onChange={(e) => setPassword(e.target.value)} />}
        </Field>
        <Field label="Confirm new password">
          {(p) => <Input {...p} type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />}
        </Field>
        {error && (
          <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
        <Button type="submit" size="lg" className="w-full" loading={busy} disabled={!password || !confirm}>
          Update password
        </Button>
      </form>
    </>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetForm />
    </Suspense>
  );
}
