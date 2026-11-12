"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Spinner } from "@/components/ui/spinner";
import { ApiError, post } from "@/lib/api";

function Verify() {
  const token = useSearchParams().get("token") || "";
  const [state, setState] = useState<"working" | "ok" | "error">(token ? "working" : "error");
  const [message, setMessage] = useState(token ? "" : "This verification link is missing its token.");
  const started = useRef(false);

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true; // tokens are single-use: never submit twice (React strict mode)
    post("/auth/verify-email", { token })
      .then(() => setState("ok"))
      .catch((e) => {
        setState("error");
        setMessage(e instanceof ApiError ? e.message : "Verification failed.");
      });
  }, [token]);

  return (
    <>
      <h1 className="text-[2rem] font-semibold tracking-[-0.025em]">
        {state === "ok" ? "Email verified" : state === "error" ? "Couldn't verify email" : "Verifying your email"}
      </h1>
      {state === "working" && <Spinner className="mt-6 size-6 text-muted" label="Verifying" />}
      {state === "ok" && <p className="mt-3 text-muted">Your address is confirmed. You can use every feature of your plan.</p>}
      {state === "error" && <p className="mt-3 text-muted">{message} You can request a new link from Settings.</p>}
      <Link href={state === "ok" ? "/app" : "/sign-in"} className="mt-6 inline-block font-medium text-action hover:underline">
        {state === "ok" ? "Open your library" : "Back to sign in"}
      </Link>
    </>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense>
      <Verify />
    </Suspense>
  );
}
