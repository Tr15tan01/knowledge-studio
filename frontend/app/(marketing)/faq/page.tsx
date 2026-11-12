import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "FAQ" };

const QA: { q: string; a: React.ReactNode }[] = [
  { q: "What file types can I upload?", a: "PDF, DOCX, TXT, Markdown, HTML, JSON and CSV. EPUB support is planned. Scanned PDFs need OCR, which isn't enabled by default; they're flagged rather than indexed empty." },
  { q: "Will it make things up?", a: "It's instructed to answer only from passages in your library and to say when the evidence isn't there. Every citation is checked against the passages it was given, and invalid ones are removed. Language models can still misread a passage, which is why every claim links to the source so you can check it." },
  { q: "Does it work in Georgian?", a: "Yes. Search and answers work across Georgian and English, and quotations keep their original language. You can choose the answer language in Settings." },
  { q: "What's the difference between a book and a document?", a: "Books are long works with chapters, usually PDFs or DOCX files. Everything else is a document. They're counted separately, and a single book is counted once however many passages or chapters it has." },
  { q: "What counts as an upload?", a: "A file the server accepts. Rejected or failed uploads don't count. Deleting a file frees its storage but doesn't give the upload back for that month." },
  { q: "How is storage measured?", a: "By the size of your original files in binary units: 1 MiB is 1,048,576 bytes and 1 GiB is 1,073,741,824 bytes. Extracted text, indexes and reports don't count against your storage." },
  { q: "What happens if I downgrade or my payment fails?", a: "Your files are never deleted. If you're over the new plan's limits, new uploads and expensive operations pause until you're back under them or upgrade. Failed payments get a short grace period." },
  { q: "Can I cancel deep research?", a: "Yes, at any time. Runs also stop on their own at fixed limits on steps, AI calls and time. Anything stopped early produces a partial report marked as incomplete." },
  { q: "Is the knowledge graph accurate?", a: "It's extracted by AI, so treat it as a map of leads. Every relationship shows the passages it came from so you can verify it." },
  {
    q: "Who can see my documents?",
    a: (
      <>
        Only you. See <Link href="/security" className="text-action underline">Privacy & security</Link> for how access is enforced and which AI provider processes your text.
      </>
    ),
  },
];

export default function FaqPage() {
  return (
    <div className="mx-auto max-w-[760px] px-4 pt-14 sm:px-6">
      <h1 className="text-[2.5rem] font-semibold leading-tight tracking-[-0.03em]">Questions and answers</h1>
      <div className="mt-10 divide-y divide-rule border-y border-rule">
        {QA.map(({ q, a }) => (
          <details key={q} className="group py-4">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-[1.05rem] font-medium marker:hidden">
              {q}
              <span className="text-xl leading-none text-faint transition-transform group-open:rotate-45" aria-hidden>
                +
              </span>
            </summary>
            <div className="mt-2.5 max-w-[65ch] text-ink-2">{a}</div>
          </details>
        ))}
      </div>
    </div>
  );
}
