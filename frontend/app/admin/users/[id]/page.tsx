"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/field";
import { Badge, ErrorState, LoadingRows, Notice, PageHeader, Section } from "@/components/ui/misc";
import { UsageMeters } from "@/features/account/usage-meters";
import { useReauth } from "@/features/auth/reauth";
import { hasRole, useUser } from "@/features/auth/session";
import { ApiError, get, post } from "@/lib/api";
import { GIB, MIB, formatDate, formatDateTime, formatNumber } from "@/lib/format";
import type { AdminPlan, AdminUserDetail } from "@/types/admin";
import { STATUS_TONE } from "@/features/admin/constants";

export default function AdminUserPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const reauth = useReauth();
  const me = useUser();
  const superAdmin = hasRole(me, "SUPER_ADMIN");
  const user = useQuery({ queryKey: ["admin-user", id], queryFn: () => get<AdminUserDetail>(`/admin/users/${id}`) });
  const [dialog, setDialog] = useState<null | "suspend" | "restore" | "adjust" | "grant" | "recovery" | "role">(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin-user", id] });

  if (user.isError) return <ErrorState error={user.error} onRetry={() => user.refetch()} />;
  if (!user.data) return <LoadingRows rows={8} />;
  const u = user.data;
  const self = me?.id === u.id;
  const close = () => setDialog(null);

  return (
    <>
      <PageHeader
        back={
          <Link href="/admin/users" className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
            <ArrowLeft className="size-4" /> Users
          </Link>
        }
        title={u.email}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[u.status] || "neutral"}>{u.status}</Badge>
            {u.role !== "USER" && <Badge tone="highlight">{u.role}</Badge>}
            {u.display_name && <span>{u.display_name}</span>}
            <span>Joined {formatDate(u.created_at)}</span>
          </span>
        }
        actions={
          <>
            <Button variant="secondary" onClick={() => setDialog("adjust")}>
              Adjust quota
            </Button>
            <Button variant="secondary" onClick={() => setDialog("grant")}>
              Grant promotion
            </Button>
            <Button variant="ghost" onClick={() => setDialog("recovery")}>
              Send recovery email
            </Button>
            {superAdmin && (
              <Button variant="ghost" onClick={() => setDialog("role")}>
                Change role
              </Button>
            )}
            {u.status === "suspended" ? (
              <Button onClick={() => setDialog("restore")}>Restore account</Button>
            ) : (
              <Button variant="danger" onClick={() => setDialog("suspend")} disabled={self || u.status !== "active"}>
                Suspend
              </Button>
            )}
          </>
        }
      />
      <Notice tone="neutral" className="mb-6">
        {u.privacy_note} Viewing this page is recorded in the audit log.
      </Notice>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Section title="Usage this period" description={`${u.entitlements.plan.name} v${u.entitlements.plan.version} (${u.entitlements.source}), ${formatDate(u.entitlements.period.start)} to ${formatDate(u.entitlements.period.end)}`}>
          <UsageMeters usage={{ entitlements: u.entitlements, usage: u.usage, storage: u.storage }} />
        </Section>
        <div className="space-y-6">
          <Section title="Account">
            <dl className="space-y-2 text-sm">
              <Row k="User ID" v={<code className="text-[0.8rem]">{u.id}</code>} />
              <Row k="Email verified" v={u.email_verified ? "Yes" : "No"} />
              <Row k="Last sign-in" v={formatDateTime(u.last_login_at)} />
              <Row k="Active sessions" v={formatNumber(u.active_sessions)} />
              <Row k="Files stored" v={formatNumber(u.content_counts.files)} />
              <Row k="Reports" v={formatNumber(u.content_counts.reports)} />
            </dl>
          </Section>
          <Section title="Subscriptions">
            {u.subscriptions.length === 0 ? (
              <p className="text-sm text-muted">No subscription records. Uses the default plan.</p>
            ) : (
              <ul className="space-y-3 text-sm">
                {u.subscriptions.map((s) => (
                  <li key={s.id}>
                    <div className="flex items-center gap-2">
                      <Badge tone={s.status === "active" ? "ok" : "warn"}>{s.status}</Badge>
                      <span>{s.source}</span>
                      {s.billing_interval && <span className="text-muted">{s.billing_interval}ly</span>}
                    </div>
                    <div className="text-[0.8rem] text-muted">
                      Plan version {s.plan_version ?? "?"}
                      {s.current_period_end && `, period ends ${formatDate(s.current_period_end)}`}
                    </div>
                    {s.paddle_subscription_id && <code className="text-[0.76rem] text-faint">{s.paddle_subscription_id}</code>}
                    {s.unmapped_price && <Notice tone="warn" className="mt-1">Paddle price isn&apos;t mapped to any plan.</Notice>}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      </div>

      <Section title="Grants and adjustments" className="mt-6">
        {u.grants.length === 0 ? (
          <p className="text-sm text-muted">None.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-[0.8rem] text-muted">
              <tr>
                <th className="py-1.5 font-medium">Kind</th>
                <th className="py-1.5 font-medium">Detail</th>
                <th className="py-1.5 font-medium">Expires</th>
                <th className="py-1.5 font-medium">Reason</th>
                <th />
              </tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {u.grants.map((g) => {
                const live = !g.revoked_at && g.expires_at && new Date(g.expires_at) > new Date();
                return (
                  <tr key={g.id} className={live ? undefined : "opacity-55"}>
                    <td className="py-2">{g.kind === "promo" ? "Promotion" : "Adjustment"}</td>
                    <td className="py-2">
                      {g.kind === "adjustment" ? `${g.delta! > 0 ? "+" : ""}${g.delta} ${g.metric?.replace(/_/g, " ")}` : g.plan_id ? "Plan grant" : Object.keys(g.overrides || {}).join(", ")}
                    </td>
                    <td className="py-2">{g.revoked_at ? "Revoked" : formatDate(g.expires_at)}</td>
                    <td className="py-2 text-muted">{g.reason}</td>
                    <td className="py-2 text-right">
                      {live && g.kind === "promo" && (
                        <RevokeGrant userId={u.id} grantId={g.id} onDone={refresh} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Recent account history" className="mt-6">
        <ul className="-my-1 divide-y divide-rule text-sm">
          {u.audit.map((a, i) => (
            <li key={i} className="flex flex-wrap justify-between gap-2 py-2">
              <span>
                <span className="font-medium">{a.action}</span>
                {a.reason && <span className="text-muted">: {a.reason}</span>}
              </span>
              <span className="tabular-nums text-muted">
                {a.actor_type} {formatDateTime(a.at)}
              </span>
            </li>
          ))}
        </ul>
      </Section>

      <ConfirmDialog
        open={dialog === "suspend"}
        onOpenChange={(v) => !v && close()}
        title="Suspend this account?"
        description="The user is signed out everywhere and can't sign in. Their data is kept."
        confirmLabel="Suspend account"
        danger
        requireReason
        onConfirm={async (reason) => {
          await post(`/admin/users/${id}/suspend`, { reason });
          refresh();
        }}
      />
      <ConfirmDialog
        open={dialog === "restore"}
        onOpenChange={(v) => !v && close()}
        title="Restore this account?"
        confirmLabel="Restore account"
        requireReason
        onConfirm={async (reason) => {
          await post(`/admin/users/${id}/restore`, { reason });
          refresh();
        }}
      />
      <ConfirmDialog
        open={dialog === "recovery"}
        onOpenChange={(v) => !v && close()}
        title="Send a password reset email?"
        description="The user receives the same reset link as from the sign-in page. You never see the link or their password."
        confirmLabel="Send email"
        requireReason
        onConfirm={async (reason) => {
          const r = await post<{ status: string }>(`/admin/users/${id}/recovery`, { reason });
          toast(r.status === "sent" ? "Reset email sent." : "Email delivery isn't configured; nothing was sent.");
          refresh();
        }}
      />
      {dialog === "adjust" && <AdjustDialog id={id} onClose={close} onDone={refresh} />}
      {dialog === "grant" && <GrantDialog id={id} onClose={close} onDone={refresh} />}
      {dialog === "role" && (
        <RoleDialog
          id={id}
          current={u.role}
          onClose={close}
          onDone={refresh}
          run={async (fn) => {
            await reauth(fn);
          }}
        />
      )}
    </>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-muted">{k}</dt>
      <dd className="text-right">{v}</dd>
    </div>
  );
}

function RevokeGrant({ userId, grantId, onDone }: { userId: string; grantId: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
        Revoke
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Revoke this promotion?"
        confirmLabel="Revoke"
        danger
        requireReason
        onConfirm={async (reason) => {
          await post(`/admin/users/${userId}/grants/${grantId}/revoke`, { reason });
          onDone();
        }}
      />
    </>
  );
}

function AdjustDialog({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: () => void }) {
  const [metric, setMetric] = useState("questions");
  const [delta, setDelta] = useState("10");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title="Adjust this period's allowance"
      description="Adds to (or removes from) the current period only. It expires when the period resets."
      size="sm"
      footer={
        <Button
          loading={busy}
          disabled={reason.trim().length < 3 || !Number(delta)}
          onClick={async () => {
            setBusy(true);
            try {
              await post(`/admin/users/${id}/quota-adjustments`, { metric, delta: Number(delta), reason });
              toast("Allowance adjusted.");
              onDone();
              onClose();
            } catch (e) {
              toast.error(e instanceof ApiError ? e.message : "Couldn't adjust.");
            } finally {
              setBusy(false);
            }
          }}
        >
          Apply adjustment
        </Button>
      }
    >
      <div className="space-y-4">
        <Field label="Allowance">
          {(f) => (
            <Select {...f} value={metric} onChange={(e) => setMetric(e.target.value)}>
              <option value="questions">AI questions</option>
              <option value="uploads">Uploads</option>
              <option value="research_reports">Research reports</option>
            </Select>
          )}
        </Field>
        <Field label="Change" hint="Use a negative number to reduce.">
          {(f) => <Input {...f} type="number" min={-10000} max={10000} value={delta} onChange={(e) => setDelta(e.target.value)} />}
        </Field>
        <Field label="Reason">{(f) => <Input {...f} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
      </div>
    </Dialog>
  );
}

function GrantDialog({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: () => void }) {
  const plans = useQuery({ queryKey: ["admin-plans"], queryFn: () => get<{ items: AdminPlan[] }>("/admin/plans") });
  const [mode, setMode] = useState<"plan" | "overrides">("plan");
  const [planId, setPlanId] = useState("");
  const [extraStorage, setExtraStorage] = useState("");
  const [questions, setQuestions] = useState("");
  const [days, setDays] = useState("30");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    try {
      const overrides: Record<string, number> = {};
      if (extraStorage) overrides.storage_limit_bytes = Math.round(Number(extraStorage) * GIB);
      if (questions) overrides.question_limit_per_period = Number(questions);
      await post(`/admin/users/${id}/grants`, {
        plan_id: mode === "plan" ? planId : null,
        overrides: mode === "overrides" ? overrides : null,
        expires_at: new Date(Date.now() + Number(days) * 86_400_000).toISOString(),
        public_note: note || null,
        reason,
      });
      toast("Promotion granted.");
      onDone();
      onClose();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Couldn't grant.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title="Grant a time-limited promotion"
      description="Promotions never involve payment and expire automatically (one year at most)."
      footer={
        <Button onClick={go} loading={busy} disabled={reason.trim().length < 3 || (mode === "plan" ? !planId : !extraStorage && !questions)}>
          Grant promotion
        </Button>
      }
    >
      <div className="space-y-4">
        <Field label="Type">
          {(f) => (
            <Select {...f} value={mode} onChange={(e) => setMode(e.target.value as "plan" | "overrides")}>
              <option value="plan">Entitlements of another plan</option>
              <option value="overrides">Specific limits</option>
            </Select>
          )}
        </Field>
        {mode === "plan" ? (
          <Field label="Plan">
            {(f) => (
              <Select {...f} value={planId} onChange={(e) => setPlanId(e.target.value)}>
                <option value="">Choose a plan</option>
                {plans.data?.items
                  .filter((p) => !p.archived_at)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </Select>
            )}
          </Field>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Storage limit (GiB)" optional hint={`Replaces the plan's limit. ${extraStorage ? `${Math.round(Number(extraStorage) * GIB / MIB)} MiB` : ""}`}>
              {(f) => <Input {...f} type="number" min={0} step="0.5" value={extraStorage} onChange={(e) => setExtraStorage(e.target.value)} />}
            </Field>
            <Field label="Questions per period" optional>
              {(f) => <Input {...f} type="number" min={0} value={questions} onChange={(e) => setQuestions(e.target.value)} />}
            </Field>
          </div>
        )}
        <Field label="Duration (days)">{(f) => <Input {...f} type="number" min={1} max={366} value={days} onChange={(e) => setDays(e.target.value)} />}</Field>
        <Field label="Note shown to the user" optional>
          {(f) => <Input {...f} value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="Thanks for beta testing" />}
        </Field>
        <Field label="Internal reason">{(f) => <Input {...f} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
      </div>
    </Dialog>
  );
}

function RoleDialog({ id, current, onClose, onDone, run }: { id: string; current: string; onClose: () => void; onDone: () => void; run: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [role, setRole] = useState(current);
  return (
    <ConfirmDialog
      open
      onOpenChange={(v) => !v && onClose()}
      title="Change role"
      description="Admins run operations. Super admins also manage plans, prices, roles and system configuration. The user is signed out to apply the change."
      confirmLabel="Change role"
      requireReason
      onConfirm={async (reason) => {
        if (role === current) throw new Error("Choose a different role.");
        await run(() => post(`/admin/users/${id}/role`, { role, reason }));
        toast("Role updated.");
        onDone();
      }}
    >
      <Select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role" className="mb-3">
        <option value="USER">User</option>
        <option value="ADMIN">Admin</option>
        <option value="SUPER_ADMIN">Super admin</option>
      </Select>
    </ConfirmDialog>
  );
}
