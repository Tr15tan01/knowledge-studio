import type { EntitlementsOut, Limits, StorageSummary, UsageResponse } from "./api";

export type EntitlementsConfig = Limits & { features: Record<string, boolean> };

export interface PriceMapping {
  id: string;
  plan_id?: string;
  plan_name?: string | null;
  interval: "month" | "year";
  paddle_price_id: string;
  active: boolean;
  verified: boolean;
  verified_price: { unit_amount_minor: number; currency: string; product_id: string | null; product_name: string | null; display_matches: boolean; checked_at: string } | null;
  environment: string | null;
  updated_at: string | null;
  updated_by?: string | null;
}

export interface AdminPlan {
  id: string;
  slug: string;
  name: string;
  description: string;
  is_active: boolean;
  is_public: boolean;
  is_default: boolean;
  display_order: number;
  archived_at: string | null;
  current_version: number;
  pricing: { currency: string; monthly_display_price_minor: number | null; annual_display_price_minor: number | null };
  marketing: { tagline?: string; highlights?: string[] };
  entitlements: EntitlementsConfig;
  subscribers_by_version: Record<string, number>;
  price_mappings: PriceMapping[];
  updated_at: string | null;
}

export interface PlanVersion {
  version: number;
  change_note: string | null;
  apply_policy: string | null;
  created_at: string;
  created_by: string;
  pricing: AdminPlan["pricing"];
  entitlements: EntitlementsConfig;
  name: string | null;
}

export interface Overview {
  period_days: number;
  users: { total: number; new: number; active: number };
  subscriptions: { paid_active: number; by_plan: Record<string, Record<string, number>> };
  mrr: { by_currency_minor: Record<string, number>; subscriptions_counted: number; is_estimate: boolean; basis: string };
  storage: { bytes: number; files: number };
  usage: { uploads: number; questions: number; research_reports: number; quota_denials: number };
  research_jobs: Record<string, number>;
  failed_jobs: number;
  provider: { calls: number; errors: number; input_tokens: number; output_tokens: number; estimated_cost_usd: number; cost_is_estimate: boolean };
  billing_events: { signature_rejections: number; [k: string]: number };
  timeseries: { date: string; registrations: number; uploads: number; questions: number; research: number; estimated_cost_usd: number }[];
}

export interface AdminUserRow {
  id: string;
  email: string;
  display_name: string | null;
  role: "USER" | "ADMIN" | "SUPER_ADMIN";
  status: string;
  email_verified: boolean;
  created_at: string | null;
  last_login_at: string | null;
  plan?: string;
  plan_source?: string;
  subscription_status?: string;
}

export interface AdminUserDetail extends AdminUserRow, Omit<UsageResponse, "entitlements"> {
  entitlements: EntitlementsOut;
  storage: StorageSummary;
  subscriptions: {
    id: string;
    source: string;
    status: string;
    plan_id: string | null;
    plan_version: number | null;
    billing_interval: string | null;
    paddle_subscription_id: string | null;
    current_period_end: string | null;
    unmapped_price: boolean;
  }[];
  grants: {
    id: string;
    kind: "promo" | "adjustment";
    metric: string | null;
    delta: number | null;
    plan_id: string | null;
    overrides: Record<string, unknown> | null;
    starts_at: string | null;
    expires_at: string | null;
    revoked_at: string | null;
    reason: string | null;
  }[];
  audit: { action: string; actor_id: string | null; actor_type: string | null; reason: string | null; at: string }[];
  active_sessions: number;
  content_counts: { files: number; reports: number };
  privacy_note: string;
}

export interface AdminJob {
  id: string;
  type: string;
  status: string;
  attempts: number;
  max_attempts: number;
  account_id: string | null;
  progress: Record<string, unknown> | null;
  error: { code: string; message: string; retryable: boolean } | null;
  retryable_by_admin: boolean;
  lease_expires_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface QueueStats {
  by_status: Record<string, number>;
  by_type: Record<string, Record<string, number>>;
  stuck: number;
  oldest_queued_seconds: number;
}

export interface HealthCheck {
  ok: boolean;
  detail: string;
  latency_ms?: number;
  required?: boolean;
}

export interface AdminHealth {
  ready: boolean;
  checks: Record<string, HealthCheck>;
  providers: Record<string, { ok: boolean; detail: string }>;
  paddle: { ok: boolean; detail: string };
  webhooks: { processed_24h: number; failed_24h: number; rejected_signatures_24h: number; last_received_at: string | null };
  workers: { id: string; seen_at: string; concurrency: number }[];
  queue: QueueStats;
}

export interface AdminConfig {
  environment: string;
  integrations: Record<string, Record<string, string | number | boolean | null>>;
  problems: string[];
  note: string;
}

export interface AuditItem {
  id: string;
  action: string;
  actor_id: string | null;
  actor_type: string | null;
  target_type: string | null;
  target_id: string | null;
  reason: string | null;
  details: Record<string, unknown> | null;
  request_id: string | null;
  created_at: string;
}

export interface BillingEvent {
  event_id: string;
  event_type: string;
  status: string;
  occurred_at: string | null;
  received_at: string;
  attempts: number | null;
  error: string | null;
  subscription_id: string | null;
}

export interface UsageReport {
  range: { from: string; to: string };
  providers: {
    provider: string;
    model: string;
    operation: string;
    kind: string;
    calls: number;
    errors: number;
    retries: number;
    input_tokens: number;
    output_tokens: number;
    estimated_cost_usd: number;
    avg_latency_ms: number;
  }[];
  embedding_requests: number;
  usage_metrics: Record<string, { total: number; events: number }>;
  quota_denials: number;
  top_accounts: { account_id: string | null; calls: number; estimated_cost_usd: number }[];
  costs_are_estimates: boolean;
}

export interface QuotaPolicy {
  upload_quota_restore_on_delete: boolean;
  replacement_counts_as_new_upload: true;
  storage_warning_thresholds: number[];
  question_credit_mode: "count" | "credits";
  operation_credits: Record<string, number>;
  failed_operation_policy: "release";
  over_limit_policy: "block_new_usage";
}

export type StorageBackend = "local" | "mongodb" | "s3";

export interface RuntimeSettings {
  storage_backend: StorageBackend;
  keep_original_files: boolean;
  registration_enabled: boolean;
  context_max_tokens: number;
  retrieval_top_k: number;
  retrieval_candidates: number;
  graph_extraction_enabled: boolean;
  platform_monthly_ai_budget_usd: number;
}

export interface AdminSettings {
  settings: RuntimeSettings;
  defaults: RuntimeSettings;
  storage_backends: StorageBackend[];
  files_held: { backend: string; files: number; bytes: number }[];
  updated_at: string | null;
  updated_by: string | null;
}

export interface ReindexMigration {
  id: string;
  status: string;
  note: string | null;
  created_at: string;
  total_chunks: number;
  embedded: number;
  remaining: number;
  complete: boolean;
  target: { provider: string; model: string; dimensions: number; vector_field: string; space_id: string };
}
