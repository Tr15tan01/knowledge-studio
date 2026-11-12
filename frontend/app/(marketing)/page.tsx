import { BookOpenText, FileSearch, Network, ShieldCheck, Sparkles } from "lucide-react";
import Link from "next/link";
import { AnswerPreview } from "@/components/marketing/answer-preview";
import { FreePlanLine } from "@/components/marketing/free-plan-line";
import { ButtonLink } from "@/components/ui/button";

const STEPS = [
  { title: "Upload", body: "Add PDFs, Word documents, Markdown, HTML, text, JSON or CSV. Books keep their chapters and page numbers." },
  { title: "Index", body: "Each file is split along its own structure and indexed for both meaning and exact wording." },
  { title: "Ask", body: "Questions are answered only from your files. Every claim carries a citation you can open." },
  { title: "Research", body: "For bigger questions, a bounded research run reads across your library and writes a cited report." },
];

export default function LandingPage() {
  return (
    <>
      <section className="mx-auto grid max-w-[1200px] items-center gap-14 px-4 pb-20 pt-14 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:pt-24">
        <div>
          <h1 className="text-[2.6rem] font-semibold leading-[1.03] tracking-[-0.035em] sm:text-[3.4rem] lg:text-[3.75rem]" style={{ fontVariationSettings: '"opsz" 96, "wdth" 85' }}>
            Your books and documents become a searchable, connected knowledge system.
          </h1>
          <p className="mt-6 max-w-[34rem] text-[1.12rem] leading-relaxed text-ink-2">
            Upload what you read. Ask questions and get answers that cite the exact page they came from. Follow ideas across books, and let a research
            assistant write reports you can check line by line.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <ButtonLink href="/register" size="lg">
              Start free
            </ButtonLink>
            <ButtonLink href="/how-it-works" size="lg" variant="secondary">
              See how it works
            </ButtonLink>
          </div>
          <FreePlanLine />
        </div>
        <AnswerPreview />
      </section>

      <section className="border-y border-rule bg-surface">
        <div className="mx-auto max-w-[1200px] px-4 py-16 sm:px-6">
          <h2 className="max-w-xl text-[2rem] font-semibold leading-tight tracking-[-0.025em]">From a stack of files to answers you can verify</h2>
          <ol className="mt-10 grid gap-x-8 gap-y-8 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((s, i) => (
              <li key={s.title} className="border-t-2 border-ink pt-4">
                <div className="text-sm tabular-nums text-muted">Step {i + 1}</div>
                <h3 className="mt-1 text-lg font-semibold">{s.title}</h3>
                <p className="mt-1.5 text-[0.95rem] text-ink-2">{s.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mx-auto max-w-[1200px] space-y-20 px-4 py-20 sm:px-6">
        <Feature
          icon={<FileSearch className="size-5" />}
          title="Answers that show their work"
          body="Knowledge Studio searches by meaning and by exact words at the same time, so it finds both the idea you described and the name you typed. Answers use only the passages it found. When your library doesn't cover a question, it says so instead of guessing."
          points={["Citations open the original passage, highlighted in context", "Works across English and Georgian, and keeps quotes in their original language", "Filter by book, collection, author or date"]}
        />
        <Feature
          icon={<Sparkles className="size-5" />}
          title="Research that stops when it should"
          body="Deep research plans sub-questions, gathers evidence for each, looks for gaps and disagreements, and writes a structured report. Every run has hard limits on steps, AI calls and time. If it reaches one, you get an honest partial report, clearly marked."
          points={["Live progress you can leave and come back to", "Supported findings and hypotheses are labelled differently", "Export to Markdown, and to PDF on plans that include it"]}
        />
        <Feature
          icon={<Network className="size-5" />}
          title="See how ideas connect"
          body="People, places, concepts and events are extracted from your files into a graph. Every relationship links to the passages behind it, and each one is marked as AI-extracted so you know to check it."
          points={["Search, filter by type, and expand neighbourhoods", "Jump from any node to the pages that mention it", "Built for large libraries: the graph loads in bounded pieces"]}
        />
      </section>

      <section className="mx-auto max-w-[1200px] px-4 sm:px-6">
        <div className="grid gap-10 rounded-[20px] bg-ink px-6 py-12 text-paper sm:px-12 lg:grid-cols-[1fr_1.2fr]">
          <div>
            <ShieldCheck className="size-7 text-highlight" />
            <h2 className="mt-4 text-[1.9rem] font-semibold leading-tight tracking-[-0.02em]">Your library stays yours</h2>
          </div>
          <ul className="space-y-4 text-[0.98rem] text-paper/85">
            <li>Files are stored privately and only ever served to your account through short-lived, authorised links.</li>
            <li>Every request is checked against your workspace on the server. Other users can&apos;t reach your files, passages or answers.</li>
            <li>Text from your documents is treated as data, never as instructions, so a hostile file can&apos;t take over the assistant.</li>
            <li>
              Export everything or delete your account at any time.{" "}
              <Link href="/security" className="text-highlight underline underline-offset-4">
                Read how we handle your data
              </Link>
            </li>
          </ul>
        </div>
      </section>

      <section className="mx-auto max-w-[1200px] px-4 pt-20 text-center sm:px-6">
        <BookOpenText className="mx-auto size-8 text-action" />
        <h2 className="mx-auto mt-4 max-w-2xl text-[2rem] font-semibold leading-tight tracking-[-0.025em]">Start with the book on your desk</h2>
        <p className="mx-auto mt-3 max-w-lg text-ink-2">Upload it, ask the question you&apos;ve been carrying around, and open the citation.</p>
        <div className="mt-7 flex justify-center gap-3">
          <ButtonLink href="/register" size="lg">
            Create your library
          </ButtonLink>
          <ButtonLink href="/pricing" size="lg" variant="ghost">
            Compare plans
          </ButtonLink>
        </div>
      </section>
    </>
  );
}

function Feature({ icon, title, body, points }: { icon: React.ReactNode; title: string; body: string; points: string[] }) {
  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_1.1fr]">
      <div>
        <div className="inline-flex rounded-full bg-action-soft p-2.5 text-action">{icon}</div>
        <h2 className="mt-4 text-[1.85rem] font-semibold leading-tight tracking-[-0.02em]">{title}</h2>
      </div>
      <div>
        <p className="text-[1.05rem] leading-relaxed text-ink-2">{body}</p>
        <ul className="mt-5 space-y-2.5">
          {points.map((p) => (
            <li key={p} className="flex gap-3 text-[0.97rem]">
              <span className="mt-2 h-1.5 w-3 shrink-0 rounded-full bg-highlight" aria-hidden />
              {p}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
