# Security and privacy

The backend is the security boundary. The frontend never makes an authorization decision that matters.

## Authentication and sessions

| Control | Implementation |
|---|---|
| Password hashing | Argon2id (`argon2-cffi`), parameters upgraded transparently on login (`needs_rehash`); hashing runs off the event loop |
| Password policy | minimum length (`PASSWORD_MIN_LENGTH`), maximum 256, rejection of common passwords |
| Sessions | 256-bit random opaque tokens in an **HttpOnly** cookie (`Secure` outside dev and test, `SameSite=Lax`). MongoDB stores only `HMAC-SHA256(SESSION_SECRET, token)`. Absolute TTL, idle timeout, periodic rotation, revocation (single, all-others, on password change, on role change, on suspension). |
| CSRF | Per-session token in a readable `ks_csrf` cookie, required as `X-CSRF-Token` on every unsafe method and compared in constant time with the session-bound hash. Unsafe requests with a foreign `Origin` are rejected before routing. |
| Re-authentication | Sensitive actions (plan, price and role changes, quota policy, re-index, account deletion) require a password confirmation within `REAUTH_WINDOW_MINUTES`. |
| Rate limits | MongoDB-backed fixed windows per IP and per email or user: login, registration, password reset, verification resend, AI questions, uploads, search, checkout. Responses include `Retry-After`. |
| Account enumeration | Registration (when verification is required), password reset and verification resend return identical responses whether or not the email exists. Login errors are generic. |
| Email verification | Optional (`EMAIL_VERIFICATION_REQUIRED`). Tokens are random, single-use, hashed at rest and expire. Unverified accounts are limited to auth and profile routes. Without an email provider the UI says nothing was sent. |
| Google OAuth | Disabled unless `GOOGLE_AUTH_ENABLED=1`. Authorization-code flow with PKCE (S256), single-use hashed `state`, `nonce`, ID-token signature verification against Google JWKS, and `iss`, `aud` and `exp` checks. Accounts are **never** merged by email automatically: an existing user must sign in and link Google from Settings. |
| Roles | `USER`, `ADMIN`, `SUPER_ADMIN`, enforced by FastAPI dependencies on every admin route. No endpoint lets a user raise their own role. The first admin is created with the CLI only. The last super admin can't be demoted or deleted. |

## Tenant isolation

* Every user-content query goes through `WorkspaceContext.scope()`, which injects `workspace_id` (resolved from a membership record, never trusted from the client).
* Objects are looked up by `{_id, workspace_id}`, never by `_id` alone. A foreign id returns **404**, not 403, so ids can't be probed.
* Vector and keyword searches filter by `workspace_id` **inside** the search stage (Atlas pre-filter), not after retrieval.
* Graph nodes, relationships, chunks (citation previews), reports, findings, conversations, research runs and downloads are all scoped the same way.
* Tests: `tests/test_storage_uploads.py`, `tests/test_rag.py` and `tests/test_graph_research.py` include cross-tenant tests that try other users' file, chunk, entity, conversation, report and job ids. The Playwright suite repeats the file checks through the real web stack.

## Uploads and files

* Type is detected from **content** (magic bytes, structure), not the extension or `Content-Type`; mismatches are rejected. Executables and archives are refused.
* Size is enforced three ways: the declared `Content-Length` against the plan, a hard byte counter while streaming, and the platform ceiling `UPLOAD_HARD_MAX_BYTES`.
* Storage keys are random. Files live outside any web root (server disk), in the application database (MongoDB) or in a private bucket (optional S3). By default the uploaded file is deleted as soon as its text is indexed, and only the extracted passages and their vectors remain. Where originals are kept, downloads go through an authorised endpoint that streams the file (or, with S3, redirects to a **short-lived signed URL**, `SIGNED_URL_TTL_SECONDS`).
* Parsing runs in an isolated subprocess with a wall-clock timeout and an address-space (memory) limit, plus page, row, archive-part and output limits, defeating decompression bombs and pathological PDFs and DOCX. Parsers never execute macros or scripts. (The memory limit uses POSIX `RLIMIT_AS`; on Windows the timeout and size limits still apply.)
* Extracted HTML is reduced to text; nothing extracted from a document is ever rendered as HTML in the app. Model output is rendered as Markdown with raw HTML disabled and links stripped (see `features/citations/citations.tsx`).
* Partial uploads and orphaned objects are cleaned up by reconciliation.

## AI-specific defences

* **Prompt injection:** retrieved text is wrapped in delimited, HTML-escaped `<source>` and `<passage>` blocks; system prompts state that this content is untrusted data whose instructions must be ignored.
* **No tools with ambient authority:** the research agent's only capability is a workspace-scoped retrieval function. It can't read the database, files, environment, billing or other users. Model-generated arguments (sub-questions, follow-up queries) are validated and length-limited like user input.
* **Citation forgery:** every citation marker is checked against the passages actually provided; unknown markers are removed and logged. Citations are objects with chunk ids resolved server-side, never URLs written by the model.
* **Unbounded loops and spend:** hard per-operation limits (AI calls, steps, retrieval iterations, tokens, wall time), per-user daily and monthly compute and cost budgets, and a platform-wide monthly budget. The model can't change its budget.
* **Structured output:** extraction, planning, critique and synthesis use JSON-schema-constrained output and server-side validation; malformed output is discarded, not executed.

## Web and HTTP hardening

* API: `Content-Security-Policy: default-src 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, `COOP`, HSTS in production, `Cache-Control: no-store` on API responses, a 1 MiB JSON body limit (uploads stream separately).
* Web: a CSP allowing only self plus Paddle's checkout domains, framing denied.
* CORS: explicit allow-list with credentials; wildcards are never used.
* Errors: a consistent JSON shape with a request id; stack traces are never returned.
* No server-side fetching of user-supplied URLs exists. If URL ingestion is added, it must resolve and pin DNS, block private, link-local and metadata ranges, limit redirects and size, and time out.

## Secrets

* All secrets come from environment variables, validated at startup. Production refuses to start with missing or insecure values.
* Secrets are never stored in MongoDB, never returned by any API (the admin configuration page shows only `configured`/`missing`), and never logged. The logger redacts known secret-bearing keys and never logs document text or prompts by default.
* The only value exposed to the browser is Paddle's client-side token, which Paddle designs to be public.
* CI runs secret scanning (gitleaks) and dependency audits (`pip-audit`, `npm audit`).

## Audit log

Security-sensitive events are written to `audit_events`: logins and failures, session revocations, password changes, account deletion requests, data exports, admin views of user details, suspensions, quota adjustments, grants, role changes, plan and price-mapping changes, job retries and cancellations, and billing state changes. The application exposes no update or delete operation for audit records. For stronger guarantees, export them to an external log store and restrict the database user's privileges on that collection.

## Privacy

**What leaves the system.** To provide the service, the configured AI provider (Google Gemini by default) receives document passages (for embeddings and as context), questions, and a short conversation history. Paddle receives the customer email and billing details it collects in checkout. The SMTP provider receives the recipient address and message. Sentry, if enabled, receives error metadata without request bodies (`send_default_pii=False`).

**Training use.** Whether an AI provider may use submitted content to improve its models depends on that provider's terms and the account tier the operator uses. Knowledge Studio makes no claim about this on the provider's behalf. Operators must check their Gemini API terms (paid and unpaid tiers differ) and state the result in the privacy policy.

**User controls.**
* **Export:** Settings > Your data creates a ZIP of metadata, conversations, reports and findings, plus the original files where the server keeps them, downloadable for 7 days.
* **Delete a file:** removes the object, the `books`/`documents` record, chunks (and their vectors), entity mentions, and relationships whose only evidence was that file. Storage is released once deletion is confirmed.
* **Delete the account:** requires password confirmation and that any paid subscription is cancelled first. All sessions are revoked immediately, and a deletion job removes files, chunks, graph, conversations, research, findings, exports, usage reservations and the user record. Audit and billing records needed for legal and fraud purposes are kept, pseudonymised by id.

**Retention.** Expired sessions, OAuth states and verification and reset tokens are removed by TTL indexes. Rate-limit and heartbeat records are TTL'd. Exports expire after 7 days. Everything else is kept until the user deletes it.

A privacy policy **template** is served at `/privacy`. It must be completed with the operator's details, processors and regions, and reviewed by counsel before production use.
