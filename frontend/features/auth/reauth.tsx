"use client";

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { ApiError, post } from "@/lib/api";

type Run = <T>(fn: () => Promise<T>) => Promise<T>;
const Ctx = createContext<Run>((fn) => fn());

/**
 * Wraps sensitive actions: if the API answers `reauth_required`, ask for the password,
 * call /auth/reauthenticate, then retry the action once.
 */
export function ReauthProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<{ resolve: () => void; reject: (e: unknown) => void } | null>(null);

  const ask = () =>
    new Promise<void>((resolve, reject) => {
      pending.current = { resolve, reject };
      setPassword("");
      setErr(null);
      setOpen(true);
    });

  const run: Run = useCallback(async (fn) => {
    try {
      return await fn();
    } catch (e) {
      if (e instanceof ApiError && e.needsReauth) {
        await ask();
        return fn();
      }
      throw e;
    }
  }, []);

  async function confirm() {
    setBusy(true);
    setErr(null);
    try {
      await post("/auth/reauthenticate", { password });
      setOpen(false);
      pending.current?.resolve();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Couldn't confirm.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Ctx.Provider value={run}>
      {children}
      <Dialog
        open={open}
        onOpenChange={(v) => {
          if (!v) {
            setOpen(false);
            pending.current?.reject(new ApiError(403, { code: "reauth_cancelled", message: "Confirmation cancelled." }));
          }
        }}
        title="Confirm it's you"
        description="This is a sensitive action. Enter your password to continue."
        size="sm"
        footer={
          <Button onClick={confirm} loading={busy} disabled={!password}>
            Confirm
          </Button>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            confirm();
          }}
        >
          <Field label="Password" error={err}>
            {(p) => <Input {...p} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />}
          </Field>
        </form>
      </Dialog>
    </Ctx.Provider>
  );
}

export const useReauth = () => useContext(Ctx);
