"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Laptop, LogOut } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { Badge, LoadingRows, Notice, PageHeader, Section, Tabs } from "@/components/ui/misc";
import { Switch } from "@/components/ui/switch";
import { ReauthProvider, useReauth } from "@/features/auth/reauth";
import { qk, useAuthConfig } from "@/features/auth/session";
import { ThemePicker } from "@/features/theme/theme-picker";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatBytes, formatDate, formatDateTime, relativeTime } from "@/lib/format";
import type { SessionItem, User } from "@/types/api";

type Tab = "profile" | "security" | "preferences" | "data";

function SettingsView() {
  const params = useSearchParams();
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "profile");
  const me = useQuery({ queryKey: ["me"], queryFn: () => get<User>("/me") });
  useEffect(() => {
    if (params.get("linked") === "google") toast.success("Google account linked.");
    if (params.get("error") === "google_already_linked") toast.error("That Google account is already linked to another Knowledge Studio account.");
  }, [params]);

  return (
    <>
      <PageHeader title="Settings" />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { value: "profile", label: "Profile" },
          { value: "security", label: "Security" },
          { value: "preferences", label: "Preferences" },
          { value: "data", label: "Your data" },
        ]}
        className="mb-6"
      />
      {!me.data ? (
        <LoadingRows rows={5} />
      ) : (
        <div className="max-w-2xl space-y-6">
          {tab === "profile" && <Profile user={me.data} />}
          {tab === "security" && <Security user={me.data} />}
          {tab === "preferences" && <Preferences user={me.data} />}
          {tab === "data" && <DataTab user={me.data} />}
        </div>
      )}
    </>
  );
}

function Profile({ user }: { user: User }) {
  const qc = useQueryClient();
  const [name, setName] = useState(user.display_name || "");
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      await patch("/me", { display_name: name.trim() });
      qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: qk.session });
      toast("Profile saved.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't save.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Section title="Profile">
      <div className="space-y-4">
        <Field label="Name">{(p) => <Input {...p} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />}</Field>
        <div className="text-sm">
          <div className="text-muted">Email</div>
          <div className="mt-0.5 flex items-center gap-2">
            {user.email} {user.email_verified ? <Badge tone="ok">Verified</Badge> : <Badge tone="warn">Not verified</Badge>}
          </div>
        </div>
        <div className="text-sm">
          <div className="text-muted">Member since</div>
          <div className="mt-0.5">{formatDate(user.created_at)}</div>
        </div>
        <Button onClick={save} loading={busy} disabled={!name.trim() || name.trim() === user.display_name}>
          Save profile
        </Button>
      </div>
    </Section>
  );
}

function Security({ user }: { user: User }) {
  const qc = useQueryClient();
  const cfg = useAuthConfig();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => get<{ items: SessionItem[] }>("/auth/sessions") });
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const min = cfg.data?.password_min_length ?? 10;

  async function changePassword() {
    setBusy(true);
    setErr(null);
    try {
      await post("/auth/password/change", { current_password: user.has_password ? current : null, new_password: next });
      setCurrent("");
      setNext("");
      qc.invalidateQueries({ queryKey: ["sessions"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      toast("Password updated. Your other sessions were signed out.");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Couldn't change password.");
    } finally {
      setBusy(false);
    }
  }
  async function revoke(id: string) {
    try {
      await del(`/auth/sessions/${id}`);
      qc.invalidateQueries({ queryKey: ["sessions"] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't sign out that session.");
    }
  }
  async function revokeOthers() {
    const r = await post<{ revoked: number }>("/auth/sessions/revoke-others");
    toast(`Signed out ${r.revoked} other ${r.revoked === 1 ? "session" : "sessions"}.`);
    qc.invalidateQueries({ queryKey: ["sessions"] });
  }

  return (
    <>
      <Section title={user.has_password ? "Change password" : "Set a password"} description={user.has_password ? undefined : "You sign in with Google. A password lets you sign in with email too and confirm sensitive actions."}>
        <div className="space-y-4">
          {user.has_password && (
            <Field label="Current password">{(p) => <Input {...p} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />}</Field>
          )}
          <Field label="New password" hint={`At least ${min} characters.`} error={err}>
            {(p) => <Input {...p} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />}
          </Field>
          <Button onClick={changePassword} loading={busy} disabled={next.length < min || (user.has_password && !current)}>
            {user.has_password ? "Change password" : "Set password"}
          </Button>
        </div>
      </Section>

      {cfg.data?.google_enabled && (
        <Section title="Google sign-in">
          {user.linked_providers?.includes("google") ? (
            <p className="text-sm">
              <Badge tone="ok">Linked</Badge> You can sign in with Google.
            </p>
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
              <span className="text-muted">Link a Google account to sign in with it.</span>
              <a href="/api/v1/auth/google/start?mode=link&next=/app/settings" className="inline-flex h-9 items-center rounded-[var(--radius-control)] border border-rule-strong px-3.5 font-medium hover:bg-surface-2">
                Link Google account
              </a>
            </div>
          )}
        </Section>
      )}

      <Section
        title="Active sessions"
        description="Devices currently signed in to your account."
        actions={
          (sessions.data?.items.length ?? 0) > 1 && (
            <Button size="sm" variant="secondary" onClick={revokeOthers}>
              <LogOut className="size-4" /> Sign out others
            </Button>
          )
        }
      >
        {!sessions.data ? (
          <LoadingRows rows={2} />
        ) : (
          <ul className="-my-2 divide-y divide-rule">
            {sessions.data.items.map((s) => (
              <li key={s.id} className="flex items-center gap-3 py-3 text-sm">
                <Laptop className="size-5 shrink-0 text-faint" />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{describeAgent(s.user_agent)}</div>
                  <div className="text-[0.8rem] text-muted">
                    {s.network ? `Network ${s.network}, ` : ""}active {relativeTime(s.last_seen_at)}, signed in {formatDateTime(s.created_at)}
                  </div>
                </div>
                {s.current ? (
                  <Badge tone="action">This device</Badge>
                ) : (
                  <Button size="sm" variant="ghost" onClick={() => revoke(s.id)}>
                    Sign out
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}

function describeAgent(ua: string | null): string {
  if (!ua) return "Unknown device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Windows/.test(ua) ? "Windows" : /Mac OS X/.test(ua) ? "macOS" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

function Preferences({ user }: { user: User }) {
  const qc = useQueryClient();
  const save = async (prefs: Partial<User["preferences"]>) => {
    try {
      await patch("/me", { preferences: prefs });
      qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: qk.session });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't save.");
    }
  };
  return (
    <Section title="Preferences">
      <div className="space-y-6">
        <div>
          <div className="mb-2 text-sm font-medium">Theme</div>
          <ThemePicker onPick={(v) => save({ theme: v })} />
          <p className="mt-2 text-[0.82rem] text-muted">System follows your device: Blue by day, Midnight when it is set to dark.</p>
        </div>
        <Switch
          checked={!!user.preferences.reduced_motion}
          onCheckedChange={(v) => {
            document.documentElement.classList.toggle("reduce-motion", v);
            save({ reduced_motion: v });
          }}
          label="Reduce motion"
          description="Turns off decorative animation. Your system setting is always respected too."
        />
        <Field label="Answer language" hint="Answers can follow your question's language or always use one language. Citations keep the source's original language.">
          {(p) => (
            <Select {...p} value={user.preferences.answer_language || "auto"} onChange={(e) => save({ answer_language: e.target.value as "auto" | "en" | "ka" })} className="max-w-xs">
              <option value="auto">Same as my question</option>
              <option value="en">English</option>
              <option value="ka">Georgian (ქართული)</option>
            </Select>
          )}
        </Field>
      </div>
    </Section>
  );
}

function DataTab({ user }: { user: User }) {
  const router = useRouter();
  const qc = useQueryClient();
  const reauth = useReauth();
  const exports = useQuery({ queryKey: ["exports"], queryFn: () => get<{ items: { id: string; size_bytes: number; created_at: string; expires_at: string }[] }>("/me/exports"), refetchInterval: 10_000 });
  const [requesting, setRequesting] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [confirmEmail, setConfirmEmail] = useState("");

  async function requestExport() {
    setRequesting(true);
    try {
      await post("/me/export");
      toast("Export started. It appears below when ready.");
      setTimeout(() => qc.invalidateQueries({ queryKey: ["exports"] }), 3000);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't start export.");
    } finally {
      setRequesting(false);
    }
  }

  return (
    <>
      <Section title="Export your data" description="A ZIP with your original files, extracted metadata, conversations, reports and findings.">
        <Button onClick={requestExport} loading={requesting} variant="secondary">
          <Download className="size-4" /> Request export
        </Button>
        {exports.data?.items.length ? (
          <ul className="mt-4 divide-y divide-rule border-t border-rule text-sm">
            {exports.data.items.map((x) => (
              <li key={x.id} className="flex items-center justify-between gap-3 py-2.5">
                <span>
                  {formatDateTime(x.created_at)} <span className="text-muted">({formatBytes(x.size_bytes)}, expires {formatDate(x.expires_at)})</span>
                </span>
                <a href={`/api/v1/me/exports/${x.id}/download`} className="font-medium text-action hover:underline">
                  Download
                </a>
              </li>
            ))}
          </ul>
        ) : null}
      </Section>

      <Section title="Plan and usage">
        <p className="text-sm text-muted">
          See storage, quotas and your subscription in{" "}
          <Link href="/app/billing" className="text-action underline">
            Usage & billing
          </Link>
          .
        </p>
      </Section>

      <Section title="Delete account">
        <Notice tone="danger" className="mb-4">
          Deleting your account removes your files, passages, embeddings, graph, conversations, reports and findings. Billing records required by law are kept. This can&apos;t be undone.
        </Notice>
        <Button variant="danger" onClick={() => setDeleteOpen(true)}>
          Delete my account
        </Button>
      </Section>

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete your account permanently?"
        description="Type your email address to confirm. If you have a paid subscription, cancel it first."
        confirmLabel="Delete account"
        danger
        onConfirm={async () => {
          if (confirmEmail.trim().toLowerCase() !== user.email.toLowerCase()) throw new Error("The email address doesn't match.");
          await reauth(() => del("/me", { confirm_email: confirmEmail.trim() }));
          qc.clear();
          router.replace("/?deleted=1");
        }}
      >
        <Input value={confirmEmail} onChange={(e) => setConfirmEmail(e.target.value)} placeholder={user.email} aria-label="Confirm email" autoComplete="off" />
      </ConfirmDialog>
    </>
  );
}

export default function SettingsPage() {
  return (
    <Suspense fallback={<LoadingRows rows={5} />}>
      <ReauthProvider>
        <SettingsView />
      </ReauthProvider>
    </Suspense>
  );
}
