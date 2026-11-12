# Administration

## Roles

| Role | Can |
|---|---|
| `USER` | use the product; access only their own workspace |
| `ADMIN` | view the overview, plans, price mappings, settings, usage and costs, jobs, system health and configuration status, audit log and billing events; view, suspend and restore users; apply quota adjustments and promotional grants; send account-recovery emails; retry and cancel eligible jobs; export usage CSV |
| `SUPER_ADMIN` | everything an admin can, plus create, edit, archive, reorder and set-default plans; migrate subscribers between versions; map, verify and deactivate Paddle prices; change roles; edit the settings, the model price table and quota policy; start embedding migrations and file reprocessing |

Every admin route checks the role on the server (`AdminAuth`/`SuperAdminAuth` dependencies). Hiding a button is never the protection. Super-admin mutations additionally require a recent password confirmation (`REAUTH_WINDOW_MINUTES`). The admin panel asks for it automatically.

## Creating the first administrator

There is no default admin account and no public admin registration.

1. Register normally in the web app with the address you'll administer from.
2. If `EMAIL_VERIFICATION_REQUIRED=1`, verify the email.
3. On a machine with the production environment variables:

```powershell
./scripts/create-admin.ps1 -Email you@example.com -Role SUPER_ADMIN
# or: python -m app.cli create-admin --email you@example.com --role SUPER_ADMIN
```

The command asks you to retype the email (pass `--yes` for automation), refuses inactive accounts and, by default, unverified ones (`--allow-unverified` when verification is disabled), writes an audit event, and never prints passwords, tokens or secrets.

To remove admin rights: `python -m app.cli demote-admin --email …` (refuses to remove the last active super admin). Super admins can also change roles in Admin > Users; that signs the user out so the new role takes effect, and blocks demoting the last super admin.

## Panel sections

* **Overview:** users (total, new, active), paid subscriptions by plan, an MRR **estimate** from verified Paddle prices, storage, uploads, questions, reports, quota denials, failed jobs, provider calls and **estimated** cost, and webhook signature rejections, with daily charts for 7 days to 12 months.
* **Plans & pricing:** see [billing-and-plans.md](billing-and-plans.md). The editor shows a diff of every changed limit before you publish, the number of affected accounts, and the apply policy. Version history is kept per plan. **Preview pricing page** renders exactly what visitors see (optionally including hidden plans).
* **Paddle prices:** map plan and interval to a Paddle price id. Mappings are verified against Paddle; each change records who made it, when, and why.
* **Users:** search by email, name or id; filter by status and role. The detail page shows plan, version and source, period usage, storage, subscriptions (including unmapped prices), grants and adjustments, and recent account history. Document contents are **not** accessible, and opening a user's detail is itself audited.
* **Usage & costs:** filter by date, plan, user, provider, model and operation. Shows totals, a per-model table (calls, errors, retries, tokens, latency, estimated cost) and the highest-cost accounts (ids only). CSV export contains per-account aggregates without emails. Also holds the **model price table** (used for estimates) and the **quota policy**.
* **Jobs:** backlog by status, stuck jobs, attempts and sanitised errors. Retry (idempotent; ingestion, graph extraction and file deletion) and cancel (not for deletions).
* **Settings:** changed by super admins, applied within a few seconds without a restart, with a required reason and an audit entry. Environment variables supply the defaults.
  * *File storage:* where uploads are held (the server's disk, or MongoDB) and whether the original is kept after indexing. Off by default: the file is deleted once its text is indexed, so users can't download it, exports contain no originals, and a later change to chunking needs a new upload. Embedding migrations still work, because they read the stored passages. A file that failed processing keeps its original for `FAILED_ORIGINAL_RETENTION_DAYS` so it can be retried. Changing the location affects new uploads only; each file is read from where it was written.
  * *Answers:* context size in tokens, passages per answer, and candidates searched. Plan limits still cap each user.
  * *AI usage:* the monthly AI budget for the whole service, and whether new files get a knowledge graph.
  * *Sign-ups:* open or closed (password and Google). Existing users can always sign in.
* **System:** dependency health, workers, webhook stats, configuration presence (never values), and embedding re-index migrations.
* **Audit & billing events:** the append-only audit log (filter by action prefix, actor, target) and Paddle webhook events with processing status and errors.

## Sensitive operations need a reason

Suspending or restoring an account, adjusting quotas, granting or revoking promotions, sending recovery, changing roles, archiving plans, deactivating price mappings, retrying or cancelling jobs, editing the settings, the quota policy or model prices, and reprocessing files all require a written reason, which is stored in the audit log.
