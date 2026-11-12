import type { ReactNode } from "react";

/** Shared frame for text-heavy marketing pages. */
export function ProsePage({ title, intro, children }: { title: string; intro?: ReactNode; children: ReactNode }) {
  return (
    <div className="mx-auto max-w-[760px] px-4 pt-14 sm:px-6">
      <h1 className="text-[2.5rem] font-semibold leading-tight tracking-[-0.03em]">{title}</h1>
      {intro && <p className="mt-4 text-[1.08rem] leading-relaxed text-ink-2">{intro}</p>}
      <div className="mt-10 space-y-12">{children}</div>
    </div>
  );
}

export function ProseSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="text-[1.35rem] font-semibold tracking-[-0.015em]">{title}</h2>
      <div className="mt-3 space-y-3 text-[1rem] leading-relaxed text-ink-2 [&_li]:pl-1 [&_strong]:font-medium [&_strong]:text-ink [&_ul]:list-disc [&_ul]:space-y-2 [&_ul]:pl-5">{children}</div>
    </section>
  );
}
