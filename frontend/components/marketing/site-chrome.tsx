"use client";

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/brand";
import { ButtonLink } from "@/components/ui/button";
import { useSession } from "@/features/auth/session";
import { cn } from "@/lib/cn";

const LINKS = [
  { href: "/features", label: "Features" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/pricing", label: "Pricing" },
  { href: "/security", label: "Privacy & security" },
  { href: "/faq", label: "FAQ" },
];

export function SiteHeader() {
  const path = usePathname();
  const { data } = useSession();
  const [open, setOpen] = useState(false);
  const signedIn = data?.authenticated;
  return (
    <header className="sticky top-0 z-40 border-b border-rule/70 bg-paper/90 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1200px] items-center gap-6 px-4 sm:px-6">
        <Logo />
        <nav className="hidden flex-1 items-center gap-1 md:flex" aria-label="Main">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} aria-current={path === l.href ? "page" : undefined} className={cn("rounded-md px-3 py-2 text-[0.93rem] hover:text-ink", path === l.href ? "text-ink" : "text-ink-2")}>
              {l.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto hidden items-center gap-2 md:flex">
          {signedIn ? (
            <ButtonLink href="/app">Open your library</ButtonLink>
          ) : (
            <>
              <Link href="/sign-in" className="rounded-md px-3 py-2 text-[0.93rem] text-ink-2 hover:text-ink">
                Sign in
              </Link>
              <ButtonLink href="/register">Start free</ButtonLink>
            </>
          )}
        </div>
        <button className="ml-auto rounded-md p-2 md:hidden" onClick={() => setOpen((v) => !v)} aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open}>
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </div>
      {open && (
        <nav
          className="border-t border-rule bg-paper px-4 pb-5 pt-2 md:hidden"
          aria-label="Main"
          onClickCapture={(e) => {
            if ((e.target as HTMLElement).closest("a")) setOpen(false);
          }}
        >
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className="block rounded-md px-2 py-2.5 text-ink-2 hover:bg-surface-2">
              {l.label}
            </Link>
          ))}
          <div className="mt-3 flex gap-2">
            {signedIn ? (
              <ButtonLink href="/app" className="flex-1">
                Open your library
              </ButtonLink>
            ) : (
              <>
                <ButtonLink href="/sign-in" variant="secondary" className="flex-1">
                  Sign in
                </ButtonLink>
                <ButtonLink href="/register" className="flex-1">
                  Start free
                </ButtonLink>
              </>
            )}
          </div>
        </nav>
      )}
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="mt-24 border-t border-rule">
      <div className="mx-auto grid max-w-[1200px] gap-8 px-4 py-12 sm:px-6 md:grid-cols-[1.4fr_1fr_1fr_1fr]">
        <div>
          <Logo />
          <p className="mt-3 max-w-xs text-sm text-muted">A private workspace for reading, questioning and researching your own books and documents.</p>
        </div>
        <div>
          <div className="mb-2 text-sm font-medium">Product</div>
          <ul className="space-y-1.5 text-sm text-ink-2">
            <li><Link href="/features" className="hover:text-ink">Features</Link></li>
            <li><Link href="/how-it-works" className="hover:text-ink">How it works</Link></li>
            <li><Link href="/pricing" className="hover:text-ink">Pricing</Link></li>
          </ul>
        </div>
        <div>
          <div className="mb-2 text-sm font-medium">Trust</div>
          <ul className="space-y-1.5 text-sm text-ink-2">
            <li><Link href="/security" className="hover:text-ink">Privacy & security</Link></li>
            <li><Link href="/privacy" className="hover:text-ink">Privacy policy</Link></li>
          </ul>
        </div>
        <div>
          <div className="mb-2 text-sm font-medium">Help</div>
          <ul className="space-y-1.5 text-sm text-ink-2">
            <li><Link href="/faq" className="hover:text-ink">FAQ</Link></li>
            <li><Link href="/sign-in" className="hover:text-ink">Sign in</Link></li>
          </ul>
        </div>
      </div>
      <div className="mx-auto max-w-[1200px] px-4 pb-8 text-[0.8rem] text-faint sm:px-6">© {new Date().getFullYear()} Knowledge Studio</div>
    </footer>
  );
}
