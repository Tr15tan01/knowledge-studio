# Operations runbook

Commands assume the backend virtualenv: `backend\.venv\Scripts\python.exe` on Windows, `backend/.venv/bin/python` elsewhere, run from `backend/` with production environment variables loaded.

## Health at a glance

* `GET /health/live`: process is up.
* `GET /health/ready`: MongoDB and object storage reachable (503 if not).
* **Admin > System**: deep checks (search indexes and dimension match, queue, workers, AI providers, Paddle, webhooks in the last 24 h) and configuration presence.
* **Admin > Jobs**: backlog, stuck (expired lease), failed.
* Structured JSON logs carry `request_id`, `job_id`, `user_ref`, `operation`, `provider`, `model`, `duration_ms`, `status` and `error_code`. Every API response has `X-Request-ID`; users see it on error messages.

## Backups and restore

**What to back up**

| Data | Where | How |
|---|---|---|
| Database | Atlas | Enable Cloud Backup with continuous point-in-time recovery on production clusters. Keep at least 7 days PITR plus daily snapshots for 30 days. |
| Original files | server disk, MongoDB or an S3 bucket (Admin > Settings) | By default originals are deleted once indexed, so there is nothing to back up beyond the database. If *Keep original files* is on: files in MongoDB are covered by the database backup; back up `LOCAL_STORAGE_DIR` yourself; for a bucket enable versioning and a lifecycle rule. |
| Configuration | secret manager | Keep `.env` values (especially `SESSION_SECRET`, Paddle, Gemini) in a secret manager, not on disk only. |

Search indexes, embeddings and graph data are derived and are restored with the database. They can also be rebuilt.

**Restore drill (quarterly)**

1. Restore the Atlas snapshot to a **new** cluster (never over production during a drill).
2. Point a staging deployment at it (`MONGODB_URI`, same `S3_BUCKET` read-only credentials or a restored copy).
3. Run `python -m app.cli setup-db` (re-applies validators and indexes) and `python -m app.cli setup-search-indexes --wait`.
4. Run `python -m app.cli reconcile` to compare file records with stored objects. Investigate any discrepancies before going live.
5. Sign in as a test user, open a file, ask a question, and (if originals are kept) download one.

**Disaster recovery (production)**

1. Put the web app in maintenance (stop `ks-web`, or serve a static page from the proxy).
2. Stop workers first, then the API.
3. Restore Atlas to the chosen point in time (Atlas UI > Backup > Restore).
4. If files were lost, restore bucket versions for the same point in time.
5. Run `setup-db`, `setup-search-indexes --wait` and `reconcile`, then start the API, workers and web.
6. Check Admin > Audit for `billing.*` events after the restore point and run billing reconciliation (below).

## Recreating search indexes

Symptoms: “Semantic search was unavailable…” notices, or Admin > System showing `vector=missing` or a dimension mismatch.

```bash
python -m app.cli setup-search-indexes --wait
```

To rebuild a damaged index, delete it in Atlas UI (Atlas Search > index > Delete), then re-run the command. While it rebuilds, keyword search keeps working and answers are labelled degraded. The JSON definitions are in `backend/scripts/atlas-search-indexes.json`.

## Changing the embedding model

Never edit `GEMINI_EMBEDDING_DIMENSIONS` or the vector index in place: old vectors would be compared with new ones.

1. Admin > System > **Start migration**: model, dimensions, and a **new** vector field (e.g. `embedding_v2`).
2. In Atlas, create a vector index for the new field with the new dimensions (copy the JSON and change `path` and `numDimensions`). Name it e.g. `chunks_vector_v2`.
3. Wait for the migration to report **complete** (it's resumable; failures retry, and progress is shown). Search continues on the old field meanwhile.
4. Switch configuration in one deploy: `GEMINI_EMBEDDING_MODEL`, `GEMINI_EMBEDDING_DIMENSIONS`, `VECTOR_FIELD=embedding_v2`, `ATLAS_VECTOR_INDEX_NAME=chunks_vector_v2`. Restart the API and workers.
5. Verify Admin > System, then delete the old index and (optionally) `$unset` the old field.

If you change **chunking** (`CHUNK_TARGET_TOKENS`, `CHUNK_OVERLAP_TOKENS`), bump `CHUNKING_VERSION` and use Admin > System > **Reprocess outdated files**.

## AI provider outage or quota exhaustion

* Symptoms: provider errors in Admin > Usage, the circuit breaker open in Admin > System, and users seeing “The AI provider is temporarily unavailable” with keyword-search fallback results.
* Questions that fail before any text is generated are **not** charged; reservations are released automatically.
* Ingestion jobs retry with backoff; after `JOB_MAX_ATTEMPTS` they fail with a retryable code. Once the provider recovers, retry them from Admin > Jobs (filter `failed`, type `ingest.file`) or ask users to press **Retry processing**.
* Invalid API key or model name: fix the environment variable and restart. Errors are classified as `provider_auth_failed` or `provider_model_not_found`.
* If the platform budget (`PLATFORM_MONTHLY_AI_BUDGET_USD`) is reached, AI operations are refused with a clear error until the next month or until the budget is raised.

## MongoDB connectivity loss

The API returns 503 from `/health/ready` and `service_unavailable` errors. Workers stop claiming and resume automatically when the driver reconnects. Jobs whose lease expired during the outage are reclaimed. No manual action is needed unless jobs exhausted their attempts; retry those from Admin > Jobs.

## File storage outage

A full or unwritable disk, or (with an S3 bucket) an unreachable bucket. Uploads fail cleanly (no quota consumed, reservations released) and downloads return errors. Ingestion jobs retry. After recovery run `python -m app.cli reconcile` to detect any file records without objects (or orphaned objects). Repairs are logged to `reconciliation_reports`.

## Failed and stuck jobs

* **Stuck** = `running` with an expired lease (worker crashed). Another worker reclaims it automatically on its next poll; if none are running, start one.
* **Failed** ingestion, graph or deletion jobs can be retried from Admin > Jobs. Retries are idempotent and don't double-charge quotas.
* **Failed research runs** can't be retried by admins (their report allowance was settled). The user starts a new run; their allowance was released if nothing was produced.
* `python -m workers.main --types ingest.file` can be run temporarily to drain one job type.

## Billing reconciliation

Run monthly, or after any incident:

1. Admin > Audit & billing events > **Paddle webhook events**, filtered `failed`. Each shows the error. Fix the cause (usually configuration) and ask Paddle to **replay** the events from the Paddle dashboard (Notifications > event > Replay). Processing is idempotent by `event_id`.
2. Compare Paddle's subscription list (Subscriptions, status active or past_due) with Admin > Users filtered by plan. Events older than the stored subscription state are ignored as stale, and whenever `PADDLE_API_KEY` is set the backend re-fetches the authoritative subscription from Paddle before applying an event, so a replayed or late event can't roll a subscription back.
3. Subscriptions marked **unmapped price** in a user's detail page are on a Paddle price that isn't mapped to any plan (the account keeps the default plan's entitlements meanwhile). Map the price in Admin > Paddle prices, then replay the subscription's latest event from Paddle so it's re-applied with the mapping, or move the customer to a mapped price in Paddle.
4. Signature rejections (Admin > System) usually mean a rotated or misconfigured `PADDLE_WEBHOOK_SECRET`.

## Storage accounting discrepancies

`maintenance.reconcile` runs every 15 minutes. It expires stale reservations and compares file records with storage objects, and the results go to `reconciliation_reports` and the logs. To run it on demand: `python -m app.cli reconcile`. It never deletes a user's stored file because of a mismatch; it marks records for investigation and removes only orphaned partial uploads.

## Security events

* Suspicious sign-ins: Admin > Audit (`auth.login_failed`, `auth.login`), filtered by actor.
* Suspend an account: Admin > Users > user > Suspend (reason required). Sessions are revoked immediately.
* Compromised admin: demote with `python -m app.cli demote-admin --email …` (refuses to remove the last super admin), rotate `SESSION_SECRET` (signs everyone out), and review `admin.*` audit events.
* Rotating secrets: Paddle webhook secret (update Paddle and env together), Gemini key, S3 keys and SMTP password can be rotated with a restart; nothing secret is stored in the database.
