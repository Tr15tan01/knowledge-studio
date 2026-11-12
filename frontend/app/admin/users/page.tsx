"use client";

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { Input, Select } from "@/components/ui/field";
import { Badge, EmptyState, ErrorState, LoadingRows, PageHeader, Pagination } from "@/components/ui/misc";
import { useDebounced, useUrlState } from "@/hooks/use-url-state";
import { get } from "@/lib/api";
import { formatDate, relativeTime } from "@/lib/format";
import { STATUS_TONE } from "@/features/admin/constants";
import type { AdminUserRow } from "@/types/admin";
import type { Paged } from "@/types/api";


function UsersView() {
  const [f, setF] = useUrlState({ q: "", status: "", role: "", page: "1" });
  const [q, setQ] = useState(f.q);
  const dq = useDebounced(q);
  useEffect(() => {
    if (dq !== f.q) setF({ q: dq, page: "1" });
  }, [dq]); // eslint-disable-line react-hooks/exhaustive-deps
  const page = Number(f.page) || 1;
  const users = useQuery({
    queryKey: ["admin-users", f],
    queryFn: () => get<Paged<AdminUserRow>>("/admin/users", { q: f.q, status: f.status, role: f.role, page, page_size: 25 }),
    placeholderData: (p) => p,
  });
  return (
    <>
      <PageHeader title="Users" description="Account status, plan and activity. Document contents are never shown here." />
      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative min-w-60 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Email, name or user ID" className="pl-9" aria-label="Search users" />
        </div>
        <Select value={f.status} onChange={(e) => setF({ status: e.target.value, page: "1" })} className="w-auto" aria-label="Status">
          <option value="">Any status</option>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
          <option value="deleting">Being deleted</option>
        </Select>
        <Select value={f.role} onChange={(e) => setF({ role: e.target.value, page: "1" })} className="w-auto" aria-label="Role">
          <option value="">Any role</option>
          <option value="USER">User</option>
          <option value="ADMIN">Admin</option>
          <option value="SUPER_ADMIN">Super admin</option>
        </Select>
      </div>
      {users.isError ? (
        <ErrorState error={users.error} onRetry={() => users.refetch()} />
      ) : !users.data ? (
        <LoadingRows rows={6} />
      ) : !users.data.items.length ? (
        <EmptyState title="No users match" />
      ) : (
        <div className="overflow-x-auto rounded-[var(--radius-panel)] border border-rule bg-surface">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="border-b border-rule text-left text-[0.8rem] text-muted">
              <tr>
                <th className="px-4 py-2.5 font-medium">User</th>
                <th className="py-2.5 font-medium">Status</th>
                <th className="py-2.5 font-medium">Plan</th>
                <th className="py-2.5 font-medium">Joined</th>
                <th className="py-2.5 pr-4 font-medium">Last sign-in</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {users.data.items.map((u) => (
                <tr key={u.id} className="hover:bg-surface-2/60">
                  <td className="px-4 py-2.5">
                    <Link href={`/admin/users/${u.id}`} className="font-medium hover:underline">
                      {u.email}
                    </Link>
                    <div className="flex items-center gap-1.5 text-muted">
                      {u.display_name || "No name"}
                      {u.role !== "USER" && <Badge tone="highlight">{u.role === "SUPER_ADMIN" ? "Super admin" : "Admin"}</Badge>}
                    </div>
                  </td>
                  <td className="py-2.5">
                    <Badge tone={STATUS_TONE[u.status] || "neutral"}>{u.status}</Badge>
                    {!u.email_verified && <div className="text-[0.76rem] text-muted">Email unverified</div>}
                  </td>
                  <td className="py-2.5">
                    {u.plan}
                    <div className="text-[0.76rem] text-muted">
                      {u.plan_source}, {u.subscription_status?.replace(/_/g, " ")}
                    </div>
                  </td>
                  <td className="py-2.5 text-muted">{formatDate(u.created_at)}</td>
                  <td className="py-2.5 pr-4 text-muted">{u.last_login_at ? relativeTime(u.last_login_at) : "Never"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {users.data && <Pagination page={page} pageSize={25} total={users.data.total} onPage={(p) => setF({ page: String(p) })} />}
    </>
  );
}

export default function AdminUsersPage() {
  return (
    <Suspense fallback={<LoadingRows rows={6} />}>
      <UsersView />
    </Suspense>
  );
}
