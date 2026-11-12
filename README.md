# Knowledge Studio

A private AI workspace for books and documents. Upload what you read, ask questions that are answered **only** from your files with page-level citations, explore an AI-extracted knowledge graph, and run bounded deep-research jobs that write cited reports.

- **Frontend:** Next.js 16 (App Router, TypeScript strict, Tailwind CSS 4, Radix UI, React Flow, Recharts)
- **Backend:** Python 3.13, FastAPI, Pydantic v2, PyMongo async (`AsyncMongoClient`)
- **Database:** MongoDB Atlas, with Atlas Vector Search and Atlas Search
- **AI:** Google Gemini (`gemini-embedding-2` embeddings, `gemini-3.8-flash` generation), behind provider interfaces
- **Billing:** Paddle Billing, with webhooks verified, idempotent and processed in order
- **No Docker, no object store.** Everything runs natively on Windows (and macOS/Linux): three processes (web, API, worker) plus hosted Atlas. Uploads are held on the server's disk or in MongoDB (an administrator chooses) and, by default, deleted once indexed.

```
knowledge-studio/
├─ frontend/        Next.js app: marketing site, library app, admin panel
├─ backend/         FastAPI API, background worker, CLI, tests
│  ├─ app/          api, auth, billing, documents, embeddings, retrieval, research, graph, jobs, …
│  ├─ workers/      worker entry point (python -m workers.main)
│  ├─ scripts/      retrieval evaluation, Atlas index definitions
│  └─ tests/        pytest suite (real MongoDB, fake AI/billing providers)
├─ scripts/         PowerShell setup and dev scripts (+ dev.sh for macOS/Linux)
├─ docs/            architecture, setup, deployment, runbook, security, billing, admin, API
└─ .env.example     every setting, documented
```

## Quick start (Windows, PowerShell)

Prerequisites: **Python 3.13**, **Node.js 22 LTS**, and a **MongoDB Atlas** cluster (the free M0 tier works for development; see [docs/setup-atlas.md](docs/setup-atlas.md)). You also need a **Gemini API key** from Google AI Studio.

The scripts run in Windows PowerShell 5.1 and PowerShell 7. If Windows blocks them ("running scripts is disabled on this system"), allow local scripts once for your account:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

```powershell
# 1. Install dependencies and create .env (generates SESSION_SECRET for you)
./scripts/setup.ps1

# 2. Edit .env: set MONGODB_URI and GEMINI_API_KEY at minimum
notepad .env

# 3. Create collections, validators, indexes, the plan catalog, and Atlas search indexes
./scripts/setup-db.ps1

# 4. Start everything (opens three terminals: API :8000, worker, web :3000)
./scripts/dev-all.ps1
```

Open http://localhost:3000, register, upload a PDF, and ask a question.

To start each process yourself, use separate terminals:

```powershell
./scripts/dev-api.ps1       # FastAPI on http://localhost:8000 (OpenAPI docs at /api/docs)
./scripts/dev-worker.ps1    # background jobs: ingestion, embeddings, graph, research, maintenance
./scripts/dev-frontend.ps1  # Next.js on http://localhost:3000
```

| Script | Purpose |
|---|---|
| `setup.ps1 [-Production] [-SkipFrontend] [-Python <path>]` | Install packages, create `.env` (never overwrites) and `frontend/.env.local` |
| `setup-db.ps1 [-SkipSearchIndexes] [-NoWait]` | Collections, validators, indexes, plan catalog, Atlas search indexes (idempotent) |
| `dev-api.ps1 [-Port]`, `dev-worker.ps1 [-Types] [-Concurrency]`, `dev-frontend.ps1 [-Port]`, `dev-all.ps1` | Development processes |
| `create-admin.ps1 -Email <email> [-Role ADMIN\|SUPER_ADMIN]` | Promote a registered account (asks you to retype the email) |
| `test.ps1 [-E2E]`, `check.ps1` | Tests; lint, type checks and dependency audits |

On macOS/Linux, `./scripts/dev.sh setup|setup-db|api|worker|web|test|check|create-admin` does the same.

**Without Atlas (local development only):** a local `mongod --replSet rs0` works if you set `VECTOR_SEARCH_BACKEND=exact` and `KEYWORD_SEARCH_BACKEND=text_index` in `.env` and run `./scripts/setup-db.ps1 -SkipSearchIndexes`. Exact vector search scans every chunk, so it is only for small libraries. The API refuses these fallbacks when `APP_ENV=production`.

### First administrator

No default admin account exists. Register normally, then promote your account from the server:

```powershell
./scripts/create-admin.ps1 -Email you@example.com -Role SUPER_ADMIN
```

See [docs/admin.md](docs/admin.md).

## How it works

```
Browser ──▶ Next.js (same origin, /api/* proxied) ──▶ FastAPI ──▶ MongoDB Atlas
                                                       │            ▲
                                                       ▼            │
                                              jobs collection ──▶ Worker(s) ──▶ Gemini / Paddle API
```

1. **Upload:** the API validates type by content, enforces per-file, storage and monthly-upload quotas with atomic reservations, streams the file to the server's disk or to MongoDB, and queues an ingestion job.
2. **Ingest:** the worker parses with structure (pages, chapters, headings, rows), chunks along that structure (about 800 tokens with 120 overlap), embeds with Gemini (one vector per chunk, dimensions validated), and stores chunks tagged with their embedding space. The uploaded file is then deleted unless administrators keep originals; everything below works from the stored passages.
3. **Ask:** hybrid retrieval runs vector and keyword search in parallel, fuses them with Reciprocal Rank Fusion, de-duplicates, adds neighbouring passages, and fits the result to a token budget. The answer is streamed, every `[S#]` marker is validated against the passages supplied, and invalid ones are removed.
4. **Research:** a durable, bounded agent plans, retrieves, critiques, follows up and synthesises. It persists progress events, honours cancellation, and resumes after a worker restart.
5. **Graph:** entities and relationships are extracted per chunk, each relationship linked to its evidence passages.

Details: [docs/architecture.md](docs/architecture.md).

## Plans, quotas and billing

The plan catalog lives in the database and is edited in the admin panel (super admins). Every limit is configurable without code changes: uploads, storage bytes, max file size, questions, research reports, concurrency, token and AI-call limits, and compute and spend budgets. Edits create immutable versions with an explicit roll-out policy. Seeded defaults:

| | Free | Premium |
|---|---|---|
| Price | $0 | $7.99 / month or $79 / year |
| Uploads per month | 5 | 20 |
| Library size (original file sizes) | 500 MiB | 5 GiB |
| Largest file | 25 MiB | 100 MiB |
| AI questions per month | 30 | 100 |
| Research reports per month | 1 | 10 |
| Concurrent research runs, active projects | 1, 1 | 2, 5 |

Premium is granted **only** by verified Paddle webhooks, never by the checkout redirect. See [docs/billing-and-plans.md](docs/billing-and-plans.md).

## Testing

```powershell
./scripts/test.ps1          # backend pytest + frontend unit tests (+ -E2E for Playwright)
```

| Suite | What it covers | Last local run |
|---|---|---|
| Backend `pytest` (152 tests + 2 opt-in external) | auth, sessions, CSRF, OAuth state; entitlements, period boundaries, concurrent reservations; storage byte accounting and reconciliation; upload validation; parsing and chunking; RAG and citation validation; research budgets and recovery; graph provenance; Paddle signature, replay, duplicate and out-of-order webhooks; admin authorization and audit; cross-tenant isolation; configuration loading; admin settings, MongoDB file storage, originals deleted after indexing | 152 passed, 2 skipped |
| Frontend `vitest` (23 tests) | binary byte formatting, API client (CSRF, error mapping, SSE parsing), citation rendering and sanitisation, plan-editor diffing, usage meters, upload pre-validation, graph layout | 23 passed |
| Playwright E2E (11 tests) | register and sign-in, upload → index → cited answer → source passage, insufficient evidence, deep research → report → report quota (UI and API), admin isolation and cross-tenant 404s, pricing from catalog, super admin edits a quota and a new user sees it, super admin moves file storage to MongoDB and originals are gone after indexing, admin vs super-admin permissions and secret masking, mobile navigation | 11 passed |

Backend tests need a MongoDB **replica set** (transactions). Use `TEST_MONGODB_URI` for an Atlas test cluster or a local `mongod --replSet rs0`; see [docs/testing.md](docs/testing.md). External APIs are never called unless `RUN_EXTERNAL_TESTS=1`.

## Documentation

| Topic | |
|---|---|
| Architecture, data model, jobs, retrieval | [docs/architecture.md](docs/architecture.md) |
| MongoDB Atlas setup and search indexes | [docs/setup-atlas.md](docs/setup-atlas.md) |
| Deploying without Docker | [docs/deployment.md](docs/deployment.md) |
| Operations runbook (backups, restore, outages, recovery) | [docs/runbook.md](docs/runbook.md) |
| Security and privacy | [docs/security-privacy.md](docs/security-privacy.md) |
| Billing, plans and quota semantics | [docs/billing-and-plans.md](docs/billing-and-plans.md) |
| Admin panel and roles | [docs/admin.md](docs/admin.md) |
| API reference | [docs/api.md](docs/api.md) (live OpenAPI at `/api/docs` outside production) |
| Testing | [docs/testing.md](docs/testing.md) |
| Known limitations and roadmap | [docs/limitations.md](docs/limitations.md) |
