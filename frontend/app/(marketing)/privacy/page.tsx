import type { Metadata } from "next";
import { ProsePage, ProseSection } from "@/components/marketing/prose-page";
import { Notice } from "@/components/ui/misc";

export const metadata: Metadata = { title: "Privacy policy" };

/**
 * TEMPLATE. The operator must complete the bracketed items and have it reviewed by counsel
 * before production use. See docs/security-privacy.md.
 */
export default function PrivacyPolicyPage() {
  return (
    <ProsePage title="Privacy policy" intro="Last updated: [date]. This policy explains what Knowledge Studio collects, why, and the choices you have.">
      <Notice tone="warn">This is a template provided with the software. The operator of this service must complete it and review it with legal counsel before relying on it.</Notice>
      <ProseSection title="Who we are">
        <p>Knowledge Studio is operated by [legal entity name], [address] (“we”). Contact: [privacy contact email].</p>
      </ProseSection>
      <ProseSection title="What we collect">
        <ul>
          <li><strong>Account data:</strong> email address, optional name, password hash, and if you use Google sign-in, your Google account identifier.</li>
          <li><strong>Content you upload:</strong> files, extracted text, passages, embeddings, extracted entities and relationships, conversations, reports and saved findings.</li>
          <li><strong>Usage data:</strong> counts of uploads, questions and reports; AI token usage; timestamps; truncated network prefix and browser description for active sessions; security audit events.</li>
          <li><strong>Billing data:</strong> handled by Paddle as merchant of record. We store Paddle customer and subscription identifiers and subscription status, never card details.</li>
        </ul>
      </ProseSection>
      <ProseSection title="How we use it">
        <ul>
          <li>To provide the service: storing, indexing and searching your library, answering questions and producing reports.</li>
          <li>To enforce plan limits, prevent abuse, and keep the service secure.</li>
          <li>To send account emails such as verification and password reset.</li>
        </ul>
        <p>We don&apos;t sell your data and we don&apos;t use your documents to train our own models.</p>
      </ProseSection>
      <ProseSection title="AI and other processors">
        <ul>
          <li><strong>[AI provider, e.g. Google Gemini API]:</strong> receives passages from your files and your questions to create embeddings and generate answers. Use of that data for model training is governed by [provider terms and the configured account tier — verify and state here].</li>
          <li><strong>MongoDB Atlas:</strong> database hosting in [region].</li>
          <li><strong>[Object storage provider]:</strong> file storage in [region].</li>
          <li><strong>Paddle:</strong> payments and invoicing.</li>
          <li><strong>[Email provider]:</strong> transactional email.</li>
          <li><strong>[Error monitoring, if enabled]:</strong> error reports without document content.</li>
        </ul>
      </ProseSection>
      <ProseSection title="Retention and deletion">
        <ul>
          <li>Content is kept until you delete it or your account. Deleting a file removes the original, its passages, embeddings and graph links.</li>
          <li>Deleting your account removes your content. We keep billing records and security audit logs for [period] where required by law or for fraud prevention.</li>
          <li>Backups are retained for [period] and then overwritten.</li>
          <li>Data exports you request are available for [7] days.</li>
        </ul>
      </ProseSection>
      <ProseSection title="Your rights">
        <p>Depending on where you live you may have the right to access, correct, export, or delete your data, and to object to or restrict processing. You can export and delete from Settings, or contact [privacy contact email].</p>
      </ProseSection>
      <ProseSection title="Changes">
        <p>We&apos;ll post changes here and, if they&apos;re significant, notify you by email.</p>
      </ProseSection>
    </ProsePage>
  );
}
