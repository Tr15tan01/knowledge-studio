"use client";

import {
  BookOpen,
  ChartNoAxesColumn,
  CreditCard,
  FileText,
  Files,
  Home,
  LogOut,
  Menu as MenuIcon,
  MessageSquareQuote,
  Moon,
  Network,
  NotebookPen,
  ScrollText,
  Settings,
  Shield,
  Sparkles,
  Sun,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Logo } from "@/components/brand";
import { UsageBar } from "@/components/ui/misc";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/menu";
import { hasRole, useSignOut, useUsage, useUser } from "@/features/auth/session";
import { useTheme } from "@/features/theme/theme";
import { cn } from "@/lib/cn";
import { formatBytes } from "@/lib/format";

type NavItem = { href: string; label: string; icon: typeof Home; exact?: boolean };
type NavGroup = { label?: string; items: NavItem[] };

export const APP_NAV: NavGroup[] = [
  { items: [{ href: "/app", label: "Dashboard", icon: Home, exact: true }] },
  {
    label: "Library",
    items: [
      { href: "/app/library", label: "All files", icon: Files },
      { href: "/app/books", label: "Books", icon: BookOpen },
      { href: "/app/documents", label: "Documents", icon: FileText },
    ],
  },
  {
    label: "Explore",
    items: [
      { href: "/app/ask", label: "Ask AI", icon: MessageSquareQuote },
      { href: "/app/research", label: "Deep research", icon: Sparkles },
      { href: "/app/graph", label: "Knowledge graph", icon: Network },
    ],
  },
  {
    label: "Keep",
    items: [
      { href: "/app/findings", label: "Saved findings", icon: NotebookPen },
      { href: "/app/reports", label: "Reports", icon: ScrollText },
    ],
  },
];

const ACCOUNT_NAV: NavItem[] = [
  { href: "/app/billing", label: "Usage & billing", icon: CreditCard },
  { href: "/app/settings", label: "Settings", icon: Settings },
];

function isActive(path: string, item: NavItem) {
  return item.exact ? path === item.href : path === item.href || path.startsWith(`${item.href}/`);
}

function NavLink({ item, path, onNavigate }: { item: NavItem; path: string; onNavigate?: () => void }) {
  const active = isActive(path, item);
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group flex items-center gap-2.5 rounded-[8px] px-2.5 py-[7px] text-[0.93rem] transition-colors",
        active ? "bg-action-soft font-medium text-action" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
      )}
    >
      <Icon className={cn("size-[17px] shrink-0", active ? "text-action" : "text-faint group-hover:text-ink-2")} strokeWidth={1.9} />
      {item.label}
    </Link>
  );
}

function StorageMini() {
  const { data } = useUsage();
  if (!data) return <div className="skeleton h-14" />;
  const s = data.storage;
  const up = data.usage.uploads;
  return (
    <Link href="/app/billing" className="block rounded-[10px] px-2.5 py-2 hover:bg-surface-2">
      <div className="flex items-baseline justify-between text-[0.8rem]">
        <span className="font-medium text-ink-2">{data.entitlements.plan.name} plan</span>
        <span className="tabular-nums text-muted">
          {formatBytes(s.used_bytes)} / {formatBytes(s.limit_bytes)}
        </span>
      </div>
      <div className="mt-1.5">
        <UsageBar used={s.used_bytes} limit={s.limit_bytes} reserved={s.reserved_bytes} label="Storage used" />
      </div>
      <div className="mt-1.5 text-[0.76rem] text-faint">
        {Math.max(0, up.limit - up.used - up.reserved)} of {up.limit} uploads left this period
      </div>
    </Link>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  const user = useUser();
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center px-4">
        <Logo href="/app" />
      </div>
      <nav className="flex-1 space-y-5 overflow-y-auto px-2.5 pb-4 pt-1" aria-label="Main">
        {APP_NAV.map((g, i) => (
          <div key={i}>
            {g.label && <div className="mb-1 px-2.5 text-[0.76rem] font-medium text-faint">{g.label}</div>}
            <div className="space-y-0.5">
              {g.items.map((it) => (
                <NavLink key={it.href} item={it} path={path} onNavigate={onNavigate} />
              ))}
            </div>
          </div>
        ))}
        <div className="space-y-0.5 border-t border-rule pt-4">
          {ACCOUNT_NAV.map((it) => (
            <NavLink key={it.href} item={it} path={path} onNavigate={onNavigate} />
          ))}
          {hasRole(user, "ADMIN") && <NavLink item={{ href: "/admin", label: "Administration", icon: Shield }} path={path} onNavigate={onNavigate} />}
        </div>
      </nav>
      <div className="space-y-1 border-t border-rule p-2.5">
        <StorageMini />
        <UserMenu />
      </div>
    </div>
  );
}

export function UserMenu() {
  const user = useUser();
  const router = useRouter();
  const signOut = useSignOut();
  const { theme, setTheme } = useTheme();
  const initials = (user?.display_name || user?.email || "?")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase())
    .join("");
  return (
    <Menu
      align="start"
      trigger={
        <button className="flex w-full items-center gap-2.5 rounded-[10px] px-2 py-2 text-left hover:bg-surface-2" aria-label="Account menu">
          <span className="grid size-8 shrink-0 place-items-center rounded-full bg-ink text-[0.78rem] font-semibold text-paper">{initials}</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">{user?.display_name || "Your account"}</span>
            <span className="block truncate text-[0.78rem] text-muted">{user?.email}</span>
          </span>
        </button>
      }
    >
      <MenuItem onSelect={() => setTheme(theme === "dark" ? "light" : "dark")}>
        {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
        {theme === "dark" ? "Light theme" : "Dark theme"}
      </MenuItem>
      <MenuItem onSelect={() => router.push("/app/settings")}>
        <Settings className="size-4" /> Settings
      </MenuItem>
      <MenuItem onSelect={() => router.push("/app/billing")}>
        <ChartNoAxesColumn className="size-4" /> Usage & billing
      </MenuItem>
      <MenuSeparator />
      <MenuItem onSelect={signOut}>
        <LogOut className="size-4" /> Sign out
      </MenuItem>
    </Menu>
  );
}

export function AppShell({ children, sidebar }: { children: ReactNode; sidebar?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const user = useUser();
  useEffect(() => {
    document.documentElement.classList.toggle("reduce-motion", !!user?.preferences?.reduced_motion);
  }, [user?.preferences?.reduced_motion]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  const side = sidebar ?? <Sidebar onNavigate={() => setOpen(false)} />;
  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[252px_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-dvh border-r border-rule bg-surface lg:block">{side}</aside>
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-rule bg-surface/95 px-4 backdrop-blur lg:hidden">
        <Logo href="/app" />
        <button onClick={() => setOpen(true)} className="rounded-md p-2 text-ink-2 hover:bg-surface-2" aria-label="Open navigation" aria-expanded={open}>
          <MenuIcon className="size-5" />
        </button>
      </header>
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-[rgba(18,21,42,0.45)]" onClick={() => setOpen(false)} />
          <div
            className="absolute inset-y-0 left-0 w-[86%] max-w-[300px] border-r border-rule bg-surface shadow-[var(--shadow-pop)]"
            onClickCapture={(e) => {
              if ((e.target as HTMLElement).closest("a")) setOpen(false);
            }}
          >
            <button onClick={() => setOpen(false)} className="absolute right-3 top-4 z-10 rounded-md p-1.5 text-muted hover:bg-surface-2" aria-label="Close navigation">
              <X className="size-5" />
            </button>
            {side}
          </div>
        </div>
      )}
      <main id="main" className="min-w-0 px-4 py-6 sm:px-8 lg:px-10 lg:py-9">
        <div className="mx-auto max-w-[1180px]">{children}</div>
      </main>
    </div>
  );
}
