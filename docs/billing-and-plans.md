# Billing, plans and quotas

Two sources of truth, deliberately separate:

* **The plan catalog** (MongoDB, edited in Admin > Plans & pricing) decides *what a plan includes*: quotas, budgets, features and display prices.
* **Paddle** decides *whether a customer has paid*: subscription status, billing period and price. The app never grants a paid plan from the browser, a checkout redirect, or a display price.

`app/billing/entitlements.py::resolve(account)` combines them. Every quota check in the API and in background workers goes through it.

## Plan catalog

A plan has an identity (`plans`) and immutable versions (`plan_versions`). Each version snapshots:

* display: name, description, tagline, highlights, currency, monthly and annual display prices (minor units);
* entitlements (validated by `app/billing/schema.py`; unknown keys are rejected): `upload_limit_per_period`, `storage_limit_bytes`, `max_file_size_bytes`, `question_limit_per_period`, `research_report_limit_per_period`, `max_concurrent_research_jobs`, `max_saved_research_projects`, `max_concurrent_jobs`, `max_input_tokens_per_operation`, `max_output_tokens_per_operation`, `max_research_steps`, `max_retrieval_iterations`, `max_ai_calls_per_operation`, `max_operation_seconds`, `daily_compute_budget`, `monthly_compute_budget`, `max_daily_cost_usd`, `max_monthly_cost_usd`, `max_pdf_export_pages`;
* features: `semantic_search`, `hybrid_search`, `basic_knowledge_graph`, `advanced_knowledge_graph`, `deep_research`, `cross_document_research`, `pdf_export`, `markdown_export`, `advanced_filters`, `saved_searches`, `priority_processing`.

Plans can be created, edited (each edit is a new version with a change note), reordered, hidden, deactivated, made default, archived and previewed. New tiers (Student, Researcher, Team…) need no code changes.

### Rolling out an edit

Changing entitlements or display prices requires an explicit confirmation and an **apply policy**:

| Policy | Effect |
|---|---|
| `new_subscribers_only` (default) | New subscriptions and default-plan accounts created from now on get the new version. Existing accounts keep the version they're pinned to. |
| `all_subscribers` | Every account on the plan moves to the new version immediately. The review step shows how many accounts that is. |
| `cohort` | Only the listed account ids move. |
| later: **Migrate subscribers** | Explicitly move accounts from one version to another, with a reason. |

Every subscription stores `plan_version`, which is its entitlement snapshot. Changing a plan's **entitlements never changes what Paddle charges.**

### Archiving

The default plan can't be archived. A plan with active accounts can only be archived if you choose to **grandfather** them (they keep it) or name a **migration target**. Only non-paid assignments are moved automatically; paid Paddle subscriptions must be moved in Paddle, so nobody is silently repriced.

### Grants and adjustments

* **Quota adjustment:** `+/- N` uploads, questions or research reports for the current period only, with a required reason. Expires at period end.
* **Promotional grant:** another plan's entitlements, or specific overrides (e.g. storage, questions), until an expiry date of at most one year, with an optional note shown to the user. Revocable. Never involves payment.

All of these are audited.

## Quota semantics

| Resource | What counts | Window |
|---|---|---|
| **Uploads** | files the server **accepted** and stored. Rejected and failed uploads don't count. Replacing a file counts as a new upload. Deleting a file does not restore an upload (configurable: Admin > Usage > Quota policy). | billing period |
| **Storage** | `original_size_bytes` of stored files (integer bytes, binary units in the UI). Extracted text, embeddings, graph data and reports are tracked separately and **not** counted. Each file counts once even though it's referenced by both a `files` and a `books`/`documents` record. | current total |
| **Max file size** | per upload, `min(plan limit, UPLOAD_HARD_MAX_BYTES)` | n/a |
| **AI questions** | one per question by default (cross-document questions can be set to count as more in the quota policy). Not charged if it fails before any text is generated, if nothing relevant was found, or if the user stops it before generation. | billing period |
| **Research reports** | one per research run that produces a report (complete or incomplete). Released if the run fails or is cancelled before producing one. | billing period |
| **Concurrent research** and **active projects** | runs currently queued or running; projects with status `active` | live |
| **Compute budgets** | weighted tokens (1 unit = 1,000 weighted tokens) across all AI calls, including internal calls. Users don't see them as credits; they bound expensive work. | UTC day and month |
| **Spend caps** | estimated USD per user per day and month, plus `PLATFORM_MONTHLY_AI_BUDGET_USD` across all users | day, month |

**Periods.** Free accounts use calendar months in **UTC** (reset at 00:00 UTC on the 1st). Paid subscriptions use their Paddle billing period; annual subscriptions get monthly windows anchored to the subscription start day (an anchor on the 31st resets on the last day of shorter months). The UI always shows the next reset date.

**Atomicity.** Before any quota-consuming work, the API creates a **reservation** (`usage_reservations`) with an idempotency key using a conditional atomic write that fails if `used + reserved + amount > limit`. Concurrent requests therefore can't exceed a limit. Reservations are **committed** into `usage_events` when the work counts, **released** when it doesn't, and **expire** (via reconciliation) if a process dies. Retried requests with the same idempotency key don't reserve twice. Tests cover parallel reservations at the limit.

**Storage flow.** Reserve the declared size → stream with a hard byte limit → verify the actual size → commit the actual bytes (release the difference) → release everything on failure. Deletion releases storage once the object is confirmed deleted, idempotently (no double release on retries). `maintenance.reconcile` compares records with stored objects every 15 minutes.

**Downgrades and over-limit accounts.** Nothing is ever deleted. If usage exceeds the new plan (e.g. 3 GiB stored on a 500 MiB plan), the policy is **block new usage**: new uploads (and any operation exceeding a limit) are refused with a clear message offering to delete files or upgrade. Existing files remain readable, searchable and downloadable.

**Upgrades.** The new plan applies as soon as Paddle confirms the change. Usage already counted this period carries over; the higher limits apply immediately.

## Paddle setup

1. Create a Paddle **sandbox** account. In *Catalog*, create a product (e.g. “Knowledge Studio Premium”) with two recurring prices: monthly ($7.99) and yearly ($79). Copy the price ids (`pri_…`).
2. *Developer tools > Authentication*: create an **API key** (`PADDLE_API_KEY`) and a **client-side token** (`PADDLE_CLIENT_TOKEN`).
3. *Developer tools > Notifications*: add a destination `https://<your-host>/api/v1/billing/webhooks/paddle` and subscribe to at least: `subscription.created`, `subscription.activated`, `subscription.updated`, `subscription.trialing`, `subscription.past_due`, `subscription.paused`, `subscription.resumed`, `subscription.canceled`, `subscription.imported`, `transaction.completed`, `transaction.paid`, `transaction.payment_failed`, `transaction.past_due`, `customer.created`, `customer.updated`. Copy the **secret key** into `PADDLE_WEBHOOK_SECRET`.
4. In *Checkout settings*, set the default payment link to your app URL and approve your domain.
5. Set `PADDLE_ENABLED=1`, `PADDLE_ENVIRONMENT=sandbox`, restart, then in Admin > **Paddle prices** map each price id to the Premium plan and interval. With Paddle enabled, the mapping is **verified** against Paddle: the price exists and is active, the currency matches, the interval is monthly or yearly with frequency 1, and the product is active. A mismatch with the display price is flagged. (Alternatively set `PADDLE_PRICE_ID_PREMIUM_MONTHLY`/`_YEARLY` before the first `setup-db`; these are mapped once as *unverified*, and you verify them in the admin panel.)
6. For local development, Paddle must reach your webhook URL. Use a tunnel (e.g. `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`) and point the notification destination at `https://<tunnel>/api/v1/billing/webhooks/paddle`.
7. Test with Paddle's sandbox test cards, then repeat in production with `PADDLE_ENVIRONMENT=production` and live credentials.

## Checkout and lifecycle

1. **Checkout:** the browser calls `POST /api/v1/billing/checkout {plan_id, interval}`. The server finds the active mapped price, creates or reuses the Paddle customer, creates a **transaction** with `custom_data.account_id`, and returns its id. Paddle.js opens the overlay for that transaction; the browser can't set an amount.
2. **Confirmation:** after payment, the success page only *polls*. Premium is granted when the **verified webhook** (`subscription.created` or `activated`) is processed and stored in `subscriptions` with the mapped plan and its current version.
3. **Webhook verification** (`app/billing/webhooks.py`): HMAC-SHA256 over `ts:raw_body` using the `Paddle-Signature` header, compared in constant time, with a timestamp tolerance against replay (`PADDLE_WEBHOOK_TOLERANCE_SECONDS`). Rejected signatures are counted (`webhook_rejections`) and never processed.
4. **Idempotency and ordering:** `billing_events.event_id` is unique; duplicates are acknowledged without reprocessing. Events older than the stored subscription state are ignored, and when an API key is configured the subscription is re-fetched from Paddle before applying any event.
5. **Statuses → entitlements:**

| Paddle status | Access |
|---|---|
| `active`, `trialing` | paid plan |
| `past_due` | paid plan for `BILLING_PAST_DUE_GRACE_DAYS` (notice shown), then default plan |
| `paused`, `canceled` | default plan; data kept |
| scheduled cancellation | paid plan until period end (notice shown); **Keep my subscription** removes the scheduled change |

6. **Plan changes:** `POST /billing/change-plan` first returns Paddle's **preview** (prorated immediate charge and next billing date); applying requires a second confirmed call. The plan updates when Paddle's webhook arrives.
7. **Payment method and invoices:** Paddle's customer portal (`POST /billing/portal`).

## When prices change

| Situation | What happens |
|---|---|
| New subscribers | Use the **currently mapped** Paddle price for the plan and interval. |
| Existing subscribers | Keep their Paddle price; mapping a new price never touches existing subscriptions. Moving them requires an explicit change in Paddle. |
| Renewals | Renew at the subscriber's existing Paddle price. |
| Upgrades and downgrades | Use the target plan's currently mapped price, previewed and confirmed by the user. |
| Annual subscribers | Unaffected until renewal, and still at their price then unless moved in Paddle. |
| Promotional grants | Independent of price; expire on their own date. |
| Display price edits | Change the pricing page only. Verification flags when display and Paddle amounts differ. |

## Billing disabled

With `PADDLE_ENABLED=0`, checkout endpoints are unavailable, the webhook route returns 404, and the UI shows that billing is disabled. Plans and quotas still apply; there is no fake “success” mode.
