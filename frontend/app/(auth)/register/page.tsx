"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/misc";
import { GoogleButton } from "@/features/auth/google-button";
import { qk, useAuthConfig } from "@/features/auth/session";
import { ApiError, post } from "@/lib/api";
import type { User } from "@/types/api";

type RegisterResult = { status: "signed_in"; user: User } | { status: "verification_required"; message: string; email_delivery_configured: boolean };

function RegisterForm() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const { data: cfg } = useAuthConfig();
  const min = cfg?.password_min_length ?? 10;
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<{ message: string; delivery: boolean } | null>(null);
  const next = params.get("next")?.startsWith("/") ? params.get("next")! : "/app";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password.length < min) {
      setError(`Use at least ${min} characters for your password.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await post<RegisterResult>("/auth/register", { email, password, display_name: name || null });
      if (res.status === "signed_in") {
        qc.setQueryData(qk.session, { authenticated: true, user: res.user, recently_authenticated: true });
        router.replace(next);
      } else {
        setPending({ message: res.message, delivery: res.email_delivery_configured });
        setBusy(false);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Registration failed.");
      setBusy(false);
    }
  }

  if (pending) {
    return (
      <>
        <h1 className="text-[2rem] font-semibold tracking-[-0.025em]">Check your inbox</h1>
        <p className="mt-3 text-muted">{pending.message}</p>
        {!pending.delivery && (
          <Notice tone="warn" className="mt-5">
            Email delivery isn&apos;t configured on this server, so no message was sent. An administrator needs to set EMAIL_PROVIDER, or disable
            EMAIL_VERIFICATION_REQUIRED for development.
          </Notice>
        )}
        <Link href="/sign-in" className="mt-6 inline-block font-medium text-action hover:underline">
          Back to sign in
        </Link>
      </>
    );
  }

  if (cfg && !cfg.registration_enabled) {
    return (
      <>
        <h1 className="text-[2rem] font-semibold tracking-[-0.025em]">Sign-ups are closed</h1>
        <p className="mt-3 text-muted">New accounts can&apos;t be created at the moment. If you already have one, you can still sign in.</p>
        <Link href="/sign-in" className="mt-6 inline-block font-medium text-action hover:underline">
          Sign in
        </Link>
      </>
    );
  }

  return (
    <>
      <h1 className="text-[2rem] font-semibold tracking-[-0.025em]">Create your library</h1>
      <p className="mt-1.5 text-muted">
        Already have an account?{" "}
        <Link href="/sign-in" className="font-medium text-action hover:underline">
          Sign in
        </Link>
      </p>
      <div className="mt-7">
        <GoogleButton next={next} label="Sign up with Google" />
        <form onSubmit={submit} className="space-y-4" noValidate>
          <Field label="Name" optional>
            {(p) => <Input {...p} autoComplete="name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />}
          </Field>
          <Field label="Email">{(p) => <Input {...p} type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
          <Field label="Password" hint={`At least ${min} characters. A short sentence works well.`}>
            {(p) => <Input {...p} type="password" autoComplete="new-password" required minLength={min} value={password} onChange={(e) => setPassword(e.target.value)} />}
          </Field>
          {error && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
              {error}
            </p>
          )}
          <Button type="submit" size="lg" className="w-full" loading={busy} disabled={!email || !password}>
            Create account
          </Button>
          <p className="text-center text-[0.8rem] text-muted">
            By creating an account you agree to the{" "}
            <Link href="/privacy" className="underline">
              privacy policy
            </Link>
            .
          </p>
        </form>
      </div>
    </>
  );
}

export default function RegisterPage() {
  return (
    <Suspense>
      <RegisterForm />
    </Suspense>
  );
}
