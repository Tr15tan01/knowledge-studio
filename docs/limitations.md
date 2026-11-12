# Known limitations and future work

This is an accurate account of what isn't done or hasn't been verified, so nothing is mistaken for complete.

## Verified vs. not yet verified against live services

| Area | Status |
|---|---|
| MongoDB behaviour (validators, indexes, transactions, queue leasing, reservations) | Tested against a real MongoDB 8.0 replica set (133 backend tests). |
| **Atlas Vector Search and Atlas Search** | The `$vectorSearch` and `$search` query code and index definitions are implemented and unit-checked, but **automated tests run with the development fallbacks** (`exact` vectors, `$text` keywords) because a local `mongod` has no `mongot`. Run `setup-search-indexes --wait` and a smoke test on your Atlas cluster before launch. |
| **Gemini** embeddings and generation | The adapters follow the current Gemini API docs (per-chunk `Content` for batch embedding, task prefixes, `output_dimensionality`, JSON-schema output), but this build was developed **without a real API key**; tests use deterministic fakes. Run `RUN_EXTERNAL_TESTS=1 EXTERNAL_GEMINI_API_KEY=… pytest -m external` once with your key. |
| **Paddle** | Webhook verification, idempotency, ordering and entitlement effects are covered by tests with signed synthetic payloads. A real sandbox checkout hasn't been run in this environment (no credentials). Do one end-to-end sandbox purchase, renewal, failed payment and cancellation before going live. |
| SMTP email | Adapter implemented; not exercised against a real mail server. |
| S3 storage (optional) | Adapter implemented (signed URLs, streaming) but not needed and not covered by tests, which use the server disk and MongoDB. Verify with your bucket if you choose it. |
| Windows | The PowerShell scripts target Windows PowerShell 5.1 and PowerShell 7, but **this build was developed and tested on Linux**. Every script was parsed and run end-to-end with PowerShell 7.5 on Linux, from a clean copy of the repository (setup, setup-db, dev-api, dev-worker, dev-frontend, create-admin, check, test). The Windows-only branches were not executed: the `py` launcher lookup, `.venv\Scripts\python.exe`, and `dev-all.ps1` opening new windows. Nor was Windows PowerShell 5.1 itself. The code avoids POSIX-only features except the parser memory limit (`RLIMIT_AS`), which is skipped on Windows. Run `./scripts/setup.ps1` and `./scripts/test.ps1` on a Windows machine to confirm. |

## Not implemented (by design for this release)

* **Other AI providers:** only Gemini is implemented, behind `LLMProvider` and `EmbeddingProvider` interfaces. OpenAI and Anthropic adapters aren't included. **Reranking** is not implemented (`RERANKER_PROVIDER=none`).
* **OCR:** scanned PDFs are detected and reported as needing OCR; no OCR adapter is bundled.
* **EPUB** and other formats: the parser registry is extensible; EPUB isn't implemented.
* **External web research:** research uses only the user's library. `FEATURE_WEB_RESEARCH` must stay `0`.
* **Team workspaces:** the data model (workspaces, memberships with roles, account vs user ids) supports them, but there's no invitation or sharing UI or API yet. Every user has one personal workspace.
* **Multi-factor authentication:** not implemented. Sensitive actions use password re-authentication.
* **Support access to user content:** intentionally absent. Admins can't read documents.
* **Antivirus scanning** of uploads: not integrated. Files are type-sniffed, never executed, and parsed in an isolated subprocess.
* **Redis queue / Neo4j graph:** MongoDB is used for both, behind interfaces that allow swapping later.
* **Database-backed AI provider configuration:** providers are configured only through environment variables (the spec's optional admin provider editor isn't built). The admin panel shows status only.
* **User-facing credit system:** question counting is per question; per-operation weights exist in the quota policy, but there's no credit wallet UI.

## Known constraints

* **Token counts are estimates** for budgeting (about 4 characters per token for Latin text, 1.6 for other scripts) unless the provider returns usage, which Gemini does for generation.
* **Cost figures are estimates** from the editable model price table; your provider bill is authoritative.
* **Uploads through the Next.js proxy are buffered** up to `UPLOAD_PROXY_MAX_BODY`. Route `/api/*` straight to FastAPI in production ([deployment.md](deployment.md)).
* **Rate limiting** uses MongoDB fixed windows. It's adequate for one region; very high traffic would benefit from a Redis limiter.
* **PDF export** needs a Unicode font for non-Latin text. It uses `PDF_FONT_PATH` if set, otherwise DejaVu Sans on Linux (`fonts-dejavu-core`) or Sylfaen on Windows. Without one, it falls back to Helvetica and adds a note that some characters may not render; Markdown export is unaffected.
* **Graph extraction quality** depends on the model; relationships are labelled AI-extracted, with confidence shown as an estimate.
* **UI language** is English. Content, search and answers support Georgian and other languages; UI translation isn't implemented.
* **`VECTOR_SEARCH_BACKEND=exact`** scans all of a workspace's chunks and is for development and tests only (refused in production).

## Suggested next steps

1. Run the Atlas, Gemini and Paddle sandbox verifications above and record the results.
2. Build a retrieval evaluation set from real books (Georgian and English) and tune chunk size, overlap and dimensions with `scripts/eval_retrieval.py`.
3. Add an OCR adapter (e.g. Google Document AI) behind `OCR_PROVIDER`.
4. Team workspaces: invitations, shared collections, per-workspace billing accounts.
5. MFA (TOTP and WebAuthn) for admins first.
6. An optional reranker (e.g. a cross-encoder or provider rerank API) behind the existing pipeline hook.
7. EPUB parser.
