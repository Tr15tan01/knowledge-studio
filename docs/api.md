# API reference

Base path: `/api/v1`. The live OpenAPI schema is at `/api/openapi.json`, with interactive docs at `/api/docs` outside production.

## Conventions

* **Auth:** session cookie `ks_session` (HttpOnly) set by `/auth/login`, `/auth/register` or the Google callback.
* **CSRF:** every `POST/PUT/PATCH/DELETE` must send `X-CSRF-Token: <value of the ks_csrf cookie>`. Requests with a foreign `Origin` are rejected (`403 origin_rejected`).
* **Workspace:** user-content routes act on the user's default workspace; `X-Workspace-Id` selects another workspace the user is a member of.
* **Idempotency:** `Idempotency-Key` is honoured on uploads, questions, research runs and checkout. Retries with the same key don't create duplicates or consume quota twice.
* **Pagination:** `?page=&page_size=`; responses are `{items, total, page, page_size}`.
* **Errors:**

```json
{"error": {"code": "quota_exceeded", "message": "…", "details": {…}, "request_id": "…", "retryable": false}}
```

| Status | Typical codes |
|---|---|
| 400/422 | `validation_failed`, `unsupported_format`, `empty_file`, `confirmation_required`, `price_interval_mismatch` |
| 401 | `unauthorized`, `session_expired` |
| 402 | `quota_exceeded` (metric in `details`) |
| 403 | `forbidden`, `csrf_failed`, `origin_rejected`, `reauth_required`, `feature_not_in_plan`, `email_unverified` |
| 404 | `not_found` (also returned for other tenants' ids) |
| 409 | `conflict`, `upload_in_progress`, `already_subscribed` |
| 411/413 | `length_required`, `file_too_large`, `payload_too_large` |
| 429 | `rate_limited` (with `Retry-After`) |
| 502/503 | `provider_error`, `provider_unavailable`, `service_unavailable` (`retryable: true`) |

## Endpoints

### Auth and account
| Method | Path | Notes |
|---|---|---|
| GET | `/auth/config` | public: Google enabled, verification required, password policy |
| POST | `/auth/register` | `{email, password, display_name?}`; signs in, or returns `verification_required` |
| POST | `/auth/login` · `/auth/logout` | |
| GET | `/auth/session` | `{authenticated, user, recently_authenticated}`; rotates the token when due |
| POST | `/auth/verify-email` · `/auth/resend-verification` | |
| POST | `/auth/password/forgot` · `/auth/password/reset` · `/auth/password/change` | |
| POST | `/auth/reauthenticate` | `{password}`; opens the sensitive-action window |
| GET/DELETE | `/auth/sessions`, `/auth/sessions/{id}` · POST `/auth/sessions/revoke-others` | |
| GET | `/auth/google/start?next=&mode=login\|link` · `/auth/google/callback` | only when enabled |
| GET/PATCH/DELETE | `/me` | profile and preferences; DELETE schedules account deletion |
| GET | `/me/usage` | entitlements, period meters, storage summary (server-calculated) |
| POST/GET | `/me/export`, `/me/exports`, `/me/exports/{id}/download` | data export |
| GET | `/plans` | public plan catalog for the pricing page |

### Library
| Method | Path | Notes |
|---|---|---|
| POST | `/files/upload?kind=auto\|book\|document&title=&collection_id=` | raw body; headers `X-Filename` (URL-encoded), `Content-Length`; returns `202 {file, job_id}` |
| GET | `/files` | filters `status, kind, q, collection_id, sort` |
| GET/DELETE | `/files/{id}` | detail includes the book or document record and job state |
| POST | `/files/{id}/retry` | failed processing only |
| GET | `/files/{id}/download` | authorised stream or signed redirect |
| GET | `/books`, `/documents` | `q, author, language` |
| GET/PATCH | `/sources/{id}` | edit title, author, publisher, date, license note (labelled as user-provided) |
| GET | `/chunks/{id}?context=0..2` | citation preview with neighbouring passages |
| GET/POST/PATCH/DELETE | `/collections`, `/collections/{id}`, POST `/collections/{id}/files` | |
| GET | `/library/stats`, `/library/activity?days=` | dashboard metrics |
| GET | `/jobs/{id}` | job status in the current workspace |

### Questions and search
| Method | Path | Notes |
|---|---|---|
| POST | `/ask/estimate` | category, expected calls and tokens, explanation for expensive questions |
| POST | `/ask` | `{question, conversation_id?, filters, answer_length}`; with `Accept: text/event-stream` streams `conversation`, `estimate`, `sources`, `delta`*, then `done` or `error` (with optional keyword `fallback`) |
| GET/PATCH/DELETE | `/conversations`, `/conversations/{id}` · GET `/conversations/{id}/export.md` | |
| POST | `/search` | `{query, filters, mode: hybrid\|semantic\|keyword, limit}`, no AI generation |
| GET/POST/DELETE | `/saved-searches` | plan feature |

### Research
| Method | Path | Notes |
|---|---|---|
| GET/POST | `/research/projects` · GET/PATCH/DELETE `/research/projects/{id}` | project limit enforced |
| POST | `/research/estimate?depth=` | |
| POST | `/research/projects/{id}/runs` | `{question?, filters, depth}`; reserves the report allowance and budgets |
| GET | `/research/runs/{id}` · POST `/research/runs/{id}/cancel` | |
| GET | `/research/runs/{id}/events?after=` | polling fallback |
| GET | `/research/runs/{id}/stream` | SSE; resumes from `Last-Event-ID`; ends with `event: end` |
| GET/DELETE | `/reports`, `/reports/{id}` · GET `/reports/{id}/export.md`, `/reports/{id}/export.pdf` | PDF is a plan feature with a page cap |
| GET/POST/DELETE | `/findings`, `/findings/{id}` | |

### Knowledge graph
| Method | Path | Notes |
|---|---|---|
| GET | `/graph/meta` | types, counts, extraction status, disclaimer |
| GET | `/graph/entities?q=&type=&source_id=` · `/graph/entities/{id}` | detail with mentions (evidence) and relationships |
| GET | `/graph/relationships/{id}` | evidence passages, confidence (estimate), provenance |
| GET | `/graph/overview?limit=&type=&source_id=` · `/graph/neighborhood/{id}?depth=1\|2` | bounded subgraphs (≤150 nodes, ≤400 edges); depth 2 and source filters are advanced features |

### Billing
| Method | Path | Notes |
|---|---|---|
| GET | `/billing/subscription` | entitlements, subscription, history, Paddle client config |
| POST | `/billing/checkout` | `{plan_id, interval}` → Paddle transaction id |
| POST | `/billing/change-plan` | preview first; `confirm: true` to apply |
| POST | `/billing/cancel` · `/billing/resume` · `/billing/portal` | |
| POST | `/billing/webhooks/paddle` | Paddle only; signature-verified; exempt from the Origin check |

### Admin (`/admin`, ADMIN unless noted; ★ = SUPER_ADMIN plus recent re-authentication)
`GET /overview?days=` · `GET /plans` · `POST /plans` ★ · `PATCH /plans/{id}` ★ · `GET /plans/{id}/history` · `POST /plans/{id}/migrate` ★ · `POST /plans/{id}/archive` ★ · `POST /plans/{id}/default` ★ · `POST /plans/reorder` (SUPER_ADMIN) · `GET /plans-preview` · `GET /price-mappings` · `PUT /price-mappings` ★ · `POST /price-mappings/{id}/verify` (SUPER_ADMIN) · `POST /price-mappings/{id}/deactivate` ★ · `GET /users` · `GET /users/{id}` · `POST /users/{id}/suspend|restore|recovery` · `POST /users/{id}/quota-adjustments` · `POST /users/{id}/grants` · `POST /users/{id}/grants/{gid}/revoke` · `POST /users/{id}/role` ★ · `GET /usage` · `GET /usage/export.csv` · `GET /model-prices` · `PUT /model-prices` (SUPER_ADMIN) · `GET /quota-policy` · `PUT /quota-policy` ★ · `GET /settings` · `PUT /settings` ★ · `GET /jobs` · `POST /jobs/{id}/retry|cancel` · `GET /health` · `GET /config` · `GET /audit` · `GET /billing/events` · `GET /reindex` · `POST /reindex` ★ · `POST /reprocess-files` ★

### Health
`GET /health/live` (process up) · `GET /health/ready` (MongoDB and storage; 503 when not ready).
