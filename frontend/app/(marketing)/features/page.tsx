import type { Metadata } from "next";
import { ProsePage, ProseSection } from "@/components/marketing/prose-page";
import { ButtonLink } from "@/components/ui/button";

export const metadata: Metadata = { title: "Features" };

export default function FeaturesPage() {
  return (
    <ProsePage title="Features" intro="Everything Knowledge Studio does is built around one rule: you should be able to check where an answer came from.">
      <ProseSection title="A library that understands structure">
        <p>Books keep their chapters, sections and page numbers. Documents keep their headings. Spreadsheets keep their row numbers. That structure is what makes a citation like “chapter 3, p. 42” possible.</p>
        <ul>
          <li>PDF, DOCX, TXT, Markdown, HTML, JSON and CSV.</li>
          <li>Books and documents are counted separately, and a book is counted once however many passages it has.</li>
          <li>Collections, search, sorting and status filters. Failed files are flagged with the reason and can be retried.</li>
          <li>Metadata such as author and publisher is labelled by where it came from: the file, detection, or your own edits.</li>
          <li>Scanned PDFs without a text layer are reported as needing OCR rather than silently indexed as empty.</li>
        </ul>
      </ProseSection>
      <ProseSection title="Hybrid search and cited answers">
        <p>Every question runs two searches at once: one by meaning, one by exact words. The results are merged, near-duplicates removed, and neighbouring passages added where they help. The answer is written only from those passages.</p>
        <ul>
          <li>Each citation is checked against the passages actually supplied. Anything that doesn&apos;t match is removed before you see it.</li>
          <li>When the library doesn&apos;t contain enough evidence, the answer says so.</li>
          <li>Interpretation is labelled separately from what the sources say. Disagreements between sources are called out.</li>
          <li>If the AI provider is down, you get the matching passages from keyword search, clearly labelled as not an AI answer.</li>
        </ul>
      </ProseSection>
      <ProseSection title="Deep research">
        <p>A research run plans sub-questions, retrieves evidence for each, critiques its own coverage, follows up on gaps within a fixed budget, and writes a report with an executive summary, findings, contradictions, cross-source connections, limitations and open questions.</p>
        <ul>
          <li>Progress is saved as it happens, so a refresh or a restarted worker doesn&apos;t lose the run.</li>
          <li>You can cancel at any time. Partial reports are marked incomplete and say why.</li>
          <li>Findings supported by sources and hypotheses are visually distinct, in the app and in exports.</li>
        </ul>
      </ProseSection>
      <ProseSection title="Knowledge graph">
        <p>Concepts, people, places, organisations, events and techniques are extracted from your files and linked by typed relationships. Each relationship stores the passages that support it.</p>
        <ul>
          <li>Search, filter by entity type and expand neighbourhoods step by step.</li>
          <li>Open any node or relationship to read its evidence and jump to the source.</li>
          <li>Relationships are labelled as AI-extracted. They&apos;re leads to check, not verified facts.</li>
        </ul>
      </ProseSection>
      <ProseSection title="Clear usage">
        <p>Your storage meter shows the exact size of your original files in binary units, alongside uploads, questions and reports for the period and when each resets. Expensive operations explain themselves before they run.</p>
      </ProseSection>
      <div className="flex gap-3">
        <ButtonLink href="/register">Start free</ButtonLink>
        <ButtonLink href="/pricing" variant="secondary">
          Compare plans
        </ButtonLink>
      </div>
    </ProsePage>
  );
}
