import type { ReactNode } from "react";
import { Logo } from "@/components/brand";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)]">
      <main id="main" className="flex flex-col px-5 py-6 sm:px-10">
        <Logo />
        <div className="mx-auto flex w-full max-w-[400px] flex-1 flex-col justify-center py-12">{children}</div>
      </main>
      <aside className="relative hidden overflow-hidden border-l border-rule bg-surface lg:flex lg:items-center lg:justify-center" aria-hidden>
        <div className="max-w-md px-12">
          <p className="prose-read text-[1.35rem] leading-[1.6] text-ink-2">
            Every answer points back to the page it came from. <span className="mark">Ask a question, then open the passage behind each claim</span> and judge it for yourself.
          </p>
          <p className="mt-6 text-sm text-muted">Your library is private to your workspace. Uploaded files are never shared with other accounts.</p>
        </div>
      </aside>
    </div>
  );
}
