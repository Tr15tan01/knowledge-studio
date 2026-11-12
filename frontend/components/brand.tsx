import Link from "next/link";
import { cn } from "@/lib/cn";

/** Wordmark: an open book whose right page carries a highlighter stroke. */
export function Logo({ className, href = "/" }: { className?: string; href?: string }) {
  return (
    <Link href={href} className={cn("inline-flex items-center gap-2 font-semibold tracking-[-0.02em] text-ink", className)} aria-label="Knowledge Studio home">
      <LogoMark className="size-7" />
      <span className="text-[1.08rem]" style={{ fontVariationSettings: '"opsz" 32, "wdth" 88' }}>
        Knowledge Studio
      </span>
    </Link>
  );
}

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <path d="M4 7.5c3.6-1.4 7.6-1.2 11 1v17c-3.4-2.2-7.4-2.4-11-1z" fill="var(--action)" />
      <path d="M28 7.5c-3.6-1.4-7.6-1.2-11 1v17c3.4-2.2 7.4-2.4 11-1z" fill="var(--surface)" stroke="var(--action)" strokeWidth="1.6" strokeLinejoin="round" />
      <rect x="18.6" y="13" width="7.6" height="3.2" rx="1.2" fill="var(--highlight)" transform="rotate(-6 22 14.6)" />
      <path d="M19.4 19.8h5.6" stroke="var(--action)" strokeWidth="1.4" strokeLinecap="round" opacity=".55" />
    </svg>
  );
}
