import type { Metadata } from "next";
import Link from "next/link";
import { ProsePage, ProseSection } from "@/components/marketing/prose-page";

export const metadata: Metadata = { title: "Privacy & security" };

export default function SecurityPage() {
  return (
    <ProsePage title="Privacy & security" intro="A plain account of how your files and questions are handled. The full policy is in our privacy policy.">
      <ProseSection title="Who can see your library">
        <ul>
          <li>Only you. Every request is authorised on the server against your workspace, including searches, citations, graph nodes and downloads.</li>
          <li>Administrators can see account and usage information to run the service. They can&apos;t browse your documents, questions or answers through the admin tools.</li>
          <li>Downloads use short-lived authorised links. Files are never placed in a public location.</li>
        </ul>
      </ProseSection>
      <ProseSection title="AI providers">
        <p>
          To index and answer, Knowledge Studio sends text to the AI provider configured by the operator of this service, by default Google&apos;s Gemini API.
          Passages from your files are sent to create embeddings and to answer questions; your question and a short part of the conversation are sent with them.
        </p>
        <p>
          Whether a provider may use that content to improve its models depends on the provider&apos;s terms and the account the operator uses. We don&apos;t make
          promises about that on the provider&apos;s behalf; check the privacy policy for the current configuration.
        </p>
      </ProseSection>
      <ProseSection title="Accounts and sessions">
        <ul>
          <li>Passwords are hashed with Argon2id. Sessions use random tokens kept in HttpOnly cookies; only a hash of each token is stored.</li>
          <li>You can see and sign out every active session from Settings. Changing your password signs out your other sessions.</li>
          <li>Sign-in, registration and password reset are rate-limited, and responses don&apos;t reveal whether an email is registered.</li>
          <li>Sensitive actions such as deleting your account ask you to confirm your password.</li>
        </ul>
      </ProseSection>
      <ProseSection title="Uploaded files">
        <ul>
          <li>File types are checked from their contents. Files are never executed. Extracted HTML is escaped before display.</li>
          <li>Parsing runs with size, page and time limits to resist malformed or malicious files.</li>
          <li>Text inside your documents is treated as data. Instructions hidden in a file can&apos;t change what the assistant is allowed to do.</li>
        </ul>
      </ProseSection>
      <ProseSection title="Payments">
        <p>Payments are processed by Paddle. Card details never reach Knowledge Studio. Your plan changes only after Paddle sends a signed confirmation, which we verify.</p>
      </ProseSection>
      <ProseSection title="Your controls">
        <ul>
          <li>Export your files, conversations, reports and findings as a ZIP from Settings.</li>
          <li>Delete individual files, or your whole account. Deletion removes the file, its passages, embeddings and graph links.</li>
          <li>
            Questions about your data? See the <Link href="/privacy" className="text-action underline">privacy policy</Link>.
          </li>
        </ul>
      </ProseSection>
    </ProsePage>
  );
}
