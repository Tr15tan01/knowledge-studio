"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/misc";
import { GoogleButton, OAUTH_ERRORS } from "@/features/auth/google-button";
import { qk } from "@/features/auth/session";
import { ApiError, post } from "@/lib/api";
import type { User } from "@/types/api";

function safeNext(n: string | null): string {
  return n && n.startsWith("/") && !n.startsWith("//") ? n : "/app";
}

function SignInForm() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(params.get("error") ? OAUTH_ERRORS[params.get("error")!] || "Sign-in failed." : null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await post<{ user: User }>("/auth/login", { email, password });
      qc.setQueryData(qk.session, { authenticated: true, user: res.user, recently_authenticated: true });
      router.replace(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed.");
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className="text-[2rem] font-semibold tracking-[-0.025em]">Sign in</h1>
      <p className="mt-1.5 text-muted">
        New here?{" "}
        <Link href={`/register${next !== "/app" ? `?next=${encodeURIComponent(next)}` : ""}`} className="font-medium text-action hover:underline">
          Create an account
        </Link>
      </p>
      {params.get("reason") === "expired" && (
        <Notice tone="warn" className="mt-5">
          Your session ended. Sign in again to continue.
        </Notice>
      )}
      {params.get("reset") === "1" && (
        <Notice tone="ok" className="mt-5">
          Password updated. Sign in with your new password.
        </Notice>
      )}
      <div className="mt-7">
        <GoogleButton next={next} />
        <form onSubmit={submit} className="space-y-4" noValidate>
          <Field label="Email">{(p) => <Input {...p} type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
          <Field label="Password">
            {(p) => <Input {...p} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}
          </Field>
          {error && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
              {error}
            </p>
          )}
          <Button type="submit" size="lg" className="w-full" loading={busy} disabled={!email || !password}>
            Sign in
          </Button>
        </form>
        <p className="mt-5 text-center text-sm">
          <Link href="/forgot-password" className="text-muted hover:text-ink hover:underline">
            Forgot your password?
          </Link>
        </p>
      </div>
    </>
  );
}

export default function SignInPage() {
  return (
    <Suspense>
      <SignInForm />
    </Suspense>
  );
}
