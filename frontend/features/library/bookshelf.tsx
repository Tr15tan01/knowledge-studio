import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

// Cloth-binding colours. They are fixed so a book keeps its colour in every theme.
const COVERS = ["#7a2e2e", "#1f3a5f", "#2f5d46", "#8a651c", "#5a2d5c", "#1f5a5e", "#6b4226", "#3d4654", "#8b3a52", "#34507a", "#4a5d23", "#7a4a1f"];

/** Well-mixed hash (FNV-1a with a final scramble): ids that differ by one character land far apart. */
function hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193);
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return h >>> 0;
}

export function Shelf({ label, children }: { label: string; children: ReactNode }) {
  return (
    <ul className="shelf" aria-label={label}>
      {children}
    </ul>
  );
}

/**
 * One volume standing on the shelf. `seed` (a stable id) picks its colour and height.
 * `tools` are controls laid over the cover (selection, menu); `veil` dims a book that
 * isn't ready and `flag` is the note shown across its foot.
 */
export function ShelfBook({
  seed,
  href,
  title,
  sub,
  kind,
  hint,
  selected,
  veil,
  flag,
  tools,
}: {
  seed: string;
  href: string;
  title: string;
  sub?: ReactNode;
  kind: "book" | "document" | null;
  hint?: string;
  selected?: boolean;
  veil?: boolean;
  flag?: ReactNode;
  tools?: ReactNode;
}) {
  const h = hash(seed);
  const style = { "--cover": COVERS[h % COVERS.length], "--book-h": `${9.75 + ((h >>> 8) % 5) * 0.55}rem` } as CSSProperties;
  return (
    <li className="shelf-slot">
      <div className="book" style={style} data-kind={kind ?? "document"} data-selected={selected || undefined}>
        <Link href={href} className="book-face" title={hint ?? title}>
          <span className="book-title">{title}</span>
          {sub && <span className="book-sub">{sub}</span>}
        </Link>
        {veil && <span className="book-veil" aria-hidden />}
        {flag && <div className="pointer-events-none absolute inset-x-1 bottom-1.5 flex justify-center">{flag}</div>}
        {tools}
      </div>
    </li>
  );
}
