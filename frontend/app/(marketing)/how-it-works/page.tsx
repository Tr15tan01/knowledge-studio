import type { Metadata } from "next";
import { ButtonLink } from "@/components/ui/button";

export const metadata: Metadata = { title: "How it works" };

const STEPS = [
  {
    title: "You upload a file",
    body: "The server checks the file's real type from its contents, not its name, and checks it against your plan's size and storage limits before accepting it. Accepted files are stored privately under a random name.",
  },
  {
    title: "It's read in the background",
    body: "A worker extracts text with its structure: chapters, headings, page numbers, rows. Language is detected. Files that can't be read, such as encrypted PDFs or scans without text, are flagged with the reason.",
  },
  {
    title: "It's split into passages",
    body: "Text is split along its own boundaries into passages of roughly 800 tokens with a small overlap, so a citation always points to a coherent piece of text.",
  },
  {
    title: "Passages are indexed twice",
    body: "Each passage gets a semantic embedding for meaning-based search and is added to a keyword index for exact names and phrases. Embeddings record the model that made them, so different models are never mixed.",
  },
  {
    title: "Concepts are linked",
    body: "In a separate step, entities and relationships are extracted into your knowledge graph, each tied to the passages that mention it.",
  },
  {
    title: "You ask, and it answers from your passages",
    body: "Both indexes are searched, results are merged and trimmed to fit the model's context, and the answer is generated from those passages only. Citations are validated before you see them.",
  },
];

export default function HowItWorksPage() {
  return (
    <div className="mx-auto max-w-[860px] px-4 pt-14 sm:px-6">
      <h1 className="text-[2.5rem] font-semibold leading-tight tracking-[-0.03em]">How it works</h1>
      <p className="mt-4 max-w-2xl text-[1.08rem] leading-relaxed text-ink-2">What happens between dropping a PDF into your library and reading a cited answer.</p>
      <ol className="mt-12">
        {STEPS.map((s, i) => (
          <li key={s.title} className="grid grid-cols-[3rem_minmax(0,1fr)] gap-4 border-t border-rule py-7 sm:grid-cols-[4.5rem_minmax(0,1fr)]">
            <span className="text-[2rem] font-semibold leading-none tabular-nums text-rule-strong sm:text-[2.6rem]">{i + 1}</span>
            <div>
              <h2 className="text-[1.25rem] font-semibold">{s.title}</h2>
              <p className="mt-2 max-w-2xl text-[1rem] leading-relaxed text-ink-2">{s.body}</p>
            </div>
          </li>
        ))}
      </ol>
      <div className="mt-6 rounded-[var(--radius-panel)] border border-rule bg-surface p-6">
        <h2 className="text-lg font-semibold">What the AI sees</h2>
        <p className="mt-2 text-ink-2">
          Only the passages selected for your question, your question, and a short slice of the conversation. Text from your files is passed as data inside
          clearly marked boundaries and the model is instructed to ignore any instructions it contains. It has no access to your account, other files,
          billing or settings.
        </p>
      </div>
      <div className="mt-10 flex gap-3">
        <ButtonLink href="/register">Try it with your own file</ButtonLink>
        <ButtonLink href="/security" variant="secondary">
          Privacy & security
        </ButtonLink>
      </div>
    </div>
  );
}
