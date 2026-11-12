"use client";

import { Activity, ArrowLeft, BarChart3, CreditCard, FileClock, Gauge, Layers, ScrollText, Server, SlidersHorizontal, Users } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { AppShell, UserMenu } from "@/components/app-shell";
import { Logo } from "@/components/brand";
import { Badge } from "@/components/ui/misc";
import { ReauthProvider } from "@/features/auth/reauth";
import { RequireSession, useUser } from "@/features/auth/session";
import { cn } from "@/lib/cn";

const NAV = [
  { href: "/admin", label: "Overview", icon: Gauge, exact: true },
  { href: "/admin/plans", label: "Plans & pricing", icon: Layers },
  { href: "/admin/prices", label: "Paddle prices", icon: CreditCard },
  { href: "/admin/users", label: "Users", icon: Users },
  { href: "/admin/usage", label: "Usage & costs", icon: BarChart3 },
  { href: "/admin/jobs", label: "Jobs", icon: FileClock },
  { href: "/admin/settings", label: "Settings", icon: SlidersHorizontal },
  { href: "/admin/system", label: "System", icon: Server },
  { href: "/admin/audit", label: "Audit & billing events", icon: ScrollText },
];

function AdminSidebar() {
  const path = usePathname();
  const user = useUser();
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center gap-2 px-4">
        <Logo href="/admin" />
      </div>
      <div className="px-4 pb-3">
        <Badge tone={user?.role === "SUPER_ADMIN" ? "highlight" : "action"}>
          <Activity className="size-3" /> {user?.role === "SUPER_ADMIN" ? "Super admin" : "Admin"}
        </Badge>
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2.5" aria-label="Administration">
        {NAV.map((n) => {
          const active = n.exact ? path === n.href : path === n.href || path.startsWith(`${n.href}/`);
          return (
            <Link
              key={n.href}
              href={n.href}
              aria-current={active ? "page" : undefined}
              className={cn("flex items-center gap-2.5 rounded-[8px] px-2.5 py-[7px] text-[0.93rem]", active ? "bg-action-soft font-medium text-action" : "text-ink-2 hover:bg-surface-2 hover:text-ink")}
            >
              <n.icon className="size-[17px]" strokeWidth={1.9} /> {n.label}
            </Link>
          );
        })}
        <div className="border-t border-rule pt-3">
          <Link href="/app" className="flex items-center gap-2.5 rounded-[8px] px-2.5 py-[7px] text-[0.93rem] text-ink-2 hover:bg-surface-2">
            <ArrowLeft className="size-[17px]" /> Back to library
          </Link>
        </div>
      </nav>
      <div className="border-t border-rule p-2.5">
        <UserMenu />
      </div>
    </div>
  );
}

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <RequireSession role="ADMIN">
      <ReauthProvider>
        <AppShell sidebar={<AdminSidebar />}>{children}</AppShell>
      </ReauthProvider>
    </RequireSession>
  );
}
