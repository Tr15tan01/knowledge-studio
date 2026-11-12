# Testing

## Backend (`backend/tests`, pytest)

The suite runs against a **real MongoDB replica set** (needed for transactions and realistic indexes). Every session uses a fresh randomly named database that is dropped afterwards. External AI and billing providers are replaced by deterministic fakes (`tests/fakes.py`); nothing calls Gemini or Paddle unless `RUN_EXTERNAL_TESTS=1`.

```powershell
$env:TEST_MONGODB_URI = "mongodb+srv://…/?retryWrites=true"   # an Atlas test cluster, or:
# $env:TEST_MONGODB_URI = "mongodb://127.0.0.1:27017/?replicaSet=rs0"
cd backend
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check . ; .venv\Scripts\mypy.exe app ; .venv\Scripts\pip-audit.exe -r requirements.txt
```

**Local replica set without Docker** (optional): install MongoDB Community Server 7+ (MSI on Windows, Homebrew on macOS), then:

```powershell
mkdir $env:TEMP\ks-mongo
mongod --replSet rs0 --dbpath $env:TEMP\ks-mongo --port 27017 --bind_ip 127.0.0.1
# in another terminal, once:
mongosh --eval "rs.initiate({_id:'rs0',members:[{_id:0,host:'127.0.0.1:27017'}]})"
```

Coverage highlights: password hashing and rehash; session expiry, idle timeout, rotation and revocation; CSRF and origin checks; OAuth state, PKCE and nonce; registration enumeration resistance; entitlement resolution; billing-period boundaries (calendar, anchored, month-end); concurrent quota reservations at the limit; storage reserve, commit, release, deletion and reconciliation; duplicate file references not double counted; upload validation (type sniffing, size, empty, interrupted streams); parsers and chunking; RRF and dedupe; citation validation and invalid-marker removal; prompt-injection containment; keyword fallback on provider failure; research budgets, cancellation, incomplete reports and recovery after a worker crash; graph provenance and invalid-edge dropping; Paddle signature verification (forged, stale, rotated secret), duplicate and out-of-order webhooks, failed payments, checkout redirects not granting access; plan versioning and roll-out policies; admin authorization (unauthenticated, user, admin, super admin), audit coverage, secrets never returned, last-super-admin protection; cross-tenant access denial across files, chunks, graph, conversations, reports and jobs; account export and deletion; embedding migrations.

## Frontend unit tests (`frontend/tests/unit`, Vitest + Testing Library)

```powershell
cd frontend
npm test            # vitest run
npm run typecheck   # tsc --noEmit
npm run lint        # eslint (Next + React hooks rules)
```

Covers binary-unit byte formatting, the API client (CSRF header, error mapping, query building, SSE parsing), citation rendering and HTML and link sanitisation, plan-editor diffing and payloads, usage meters showing server values, upload pre-validation, and graph layout stability.

## End-to-end tests (`frontend/tests/e2e`, Playwright)

The E2E suite starts the **real** FastAPI app plus a worker (`backend/tests/e2e_server.py`, which only runs with `APP_ENV=test` and swaps in the fake AI providers), builds and serves the production frontend, and drives Chromium (desktop plus a Pixel 7 profile).

```powershell
npx playwright install chromium     # once
$env:E2E_MONGODB_URI = "mongodb://127.0.0.1:27017/?replicaSet=rs0"
npm run e2e
```

Scenarios: register, sign out, wrong password, sign in; upload → indexing → cited answer → open the highlighted source passage → source details; insufficient evidence; deep research with live progress → report with citations and hypothesis labels → report quota blocks a second run in the UI **and** the API (402); a user is redirected from `/admin`, gets 403 from admin APIs, and gets 404 for another user's file and download; pricing rendered from the catalog; a super admin raises the Free question limit in the plan editor (with review and apply-to-all) and a new user sees “0 of 42”, and the audit log records it; an ordinary admin can view but not modify plans and the config API exposes no secrets; mobile navigation and no horizontal scroll.

Paddle checkout itself isn't automated (it runs in Paddle's iframe); webhook handling is covered by backend tests. To test checkout manually, use the sandbox ([billing-and-plans.md](billing-and-plans.md#paddle-setup)).

## Retrieval evaluation

```powershell
cd backend
.venv\Scripts\python.exe -m scripts.eval_retrieval --workspace <workspace id> --dataset scripts\eval_dataset.example.json --k 8   # add --answers to also score cited answers
```

The dataset lists questions, each with the expected source title and a distinctive phrase the relevant passage contains. The script reports Recall@K, MRR and citation accuracy against a real workspace. Build a dataset from your own books to compare chunk sizes, embedding dimensions or rerankers before changing defaults.
