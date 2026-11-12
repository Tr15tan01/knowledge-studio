/** Response shapes of the Knowledge Studio API (/api/v1). Mirrors backend serializers. */

export type Role = "USER" | "ADMIN" | "SUPER_ADMIN";

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  role: Role;
  email_verified: boolean;
  has_password: boolean;
  default_workspace_id: string | null;
  preferences: { theme?: "system" | "light" | "dark"; reduced_motion?: boolean; answer_language?: "auto" | "en" | "ka" };
  created_at: string | null;
  linked_providers?: string[];
}

export interface SessionResponse {
  authenticated: boolean;
  user?: User;
  recently_authenticated?: boolean;
}

export interface AuthConfig {
  google_enabled: boolean;
  registration_enabled: boolean;
  email_verification_required: boolean;
  email_delivery_configured: boolean;
  password_min_length: number;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Feature {
  key: string;
  label: string;
  enabled: boolean;
}

export interface Limits {
  upload_limit_per_period: number;
  storage_limit_bytes: number;
  max_file_size_bytes: number;
  question_limit_per_period: number;
  research_report_limit_per_period: number;
  max_concurrent_research_jobs: number;
  max_saved_research_projects: number;
  max_concurrent_jobs: number;
  max_input_tokens_per_operation: number;
  max_output_tokens_per_operation: number;
  max_research_steps: number;
  max_retrieval_iterations: number;
  max_ai_calls_per_operation: number;
  max_operation_seconds: number;
  daily_compute_budget: number;
  monthly_compute_budget: number;
  max_daily_cost_usd: number;
  max_monthly_cost_usd: number;
  max_pdf_export_pages: number;
}

export interface EntitlementsOut {
  plan: { id: string; slug: string; name: string; version: number };
  source: string;
  subscription_status: string;
  notices: string[];
  period: { start: string; end: string; kind: string; timezone: string };
  limits: Limits;
  features: Feature[];
  active_grants: { kind: string; expires_at: string; reason: string | null }[];
}

export interface Meter {
  used: number;
  reserved: number;
  limit: number;
  period_start: string | null;
  resets_at: string | null;
}

export interface StorageSummary {
  used_bytes: number;
  reserved_bytes: number;
  limit_bytes: number;
  available_bytes: number;
  max_file_size_bytes: number;
  percent_used: number;
  warning_level: number | null;
  over_limit: boolean;
  display: { used: string; limit: string; available: string; max_file_size: string };
  breakdown: {
    stored_bytes: number;
    book_bytes: number;
    document_bytes: number;
    stored_files: number;
    uploading_files: number;
    uploading_bytes: number;
    deleting_files: number;
    failed_uploads: number;
  };
  unit_system: string;
}

export interface UsageResponse {
  entitlements: EntitlementsOut;
  usage: Record<"uploads" | "questions" | "research_reports" | "storage_bytes" | "concurrent_research" | "compute_daily" | "compute_monthly", Meter>;
  storage: StorageSummary;
}

export interface PublicPlan {
  id: string;
  slug: string;
  name: string;
  description: string;
  tagline: string;
  highlights: string[];
  is_default: boolean;
  is_public?: boolean;
  version: number;
  currency: string;
  monthly_price_minor: number | null;
  annual_price_minor: number | null;
  checkout_available: { month: boolean; year: boolean };
  limits: {
    uploads_per_period: number;
    storage_bytes: number;
    storage_display: string;
    max_file_size_bytes: number;
    max_file_size_display: string;
    questions_per_period: number;
    research_reports_per_period: number;
    concurrent_research_jobs: number;
    saved_research_projects: number;
  };
  features: Feature[];
}

export interface PlansResponse {
  plans: PublicPlan[];
  billing_enabled: boolean;
  notes: string[];
}

export type ProcessingStatus = "uploading" | "queued" | "processing" | "retrying" | "indexed" | "failed" | "rejected";

export interface JobProgress {
  phase?: string;
  completed_steps?: number;
  total_steps?: number | null;
  [k: string]: unknown;
}

export interface FileItem {
  id: string;
  title: string | null;
  filename: string;
  kind: "book" | "document" | null;
  file_type: string;
  size_bytes: number;
  size_display: string;
  storage_status: string;
  /** False once the server has deleted the original after indexing it. */
  original_available: boolean;
  processing_status: ProcessingStatus;
  processing_error: { code: string | null; message: string | null } | null;
  progress: JobProgress | null;
  job_id: string | null;
  source_id: string | null;
  language: string | null;
  chunk_count: number;
  collection_ids: string[];
  created_at: string | null;
  indexed_at: string | null;
  source?: Source;
  job?: { status: string; attempts: number; error: { code?: string; message?: string } | null } | null;
}

export interface MetaValue {
  value: string | number;
  provenance: "extracted" | "detected" | "user" | "estimated" | string;
  confidence?: number;
}

export interface OutlineEntry {
  title: string;
  level: number;
  page_start: number | null;
  page_end: number | null;
  blocks?: number;
}

export interface Source {
  id: string;
  kind: "book" | "document" | null;
  file_id: string;
  title: string | null;
  author: string | null;
  publisher: string | null;
  publication_date: string | null;
  language: string | null;
  page_count: number | null;
  file_type: string;
  status: string;
  chunk_count: number;
  metadata: Record<string, MetaValue | null> | null;
  outline: OutlineEntry[];
  warnings: string[];
  license_note: string | null;
  created_at: string | null;
}

export interface LibraryStats {
  files_uploaded: number;
  books: number;
  documents: number;
  files_processing: number;
  files_need_attention: number;
  files_indexed: number;
  failed_uploads: number;
  indexed_chunks: number;
  entities: number;
  relationships: number;
  reports: number;
  conversations: number;
}

export interface Collection {
  id: string;
  name: string;
  description: string;
  file_count: number;
}

export interface Job {
  id: string;
  type: string;
  status: "queued" | "running" | "retrying" | "completed" | "failed" | "cancelled";
  progress: JobProgress | null;
  attempts: number;
  error: { code: string; message: string; retryable: boolean } | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface Hit {
  chunk_id: string;
  source_id: string;
  file_id: string;
  source_type: "book" | "document";
  source_title: string;
  section_path: string[];
  page_start: number | null;
  page_end: number | null;
  citation_label: string | null;
  snippet: string;
  scores?: Record<string, number>;
}

export interface Citation extends Hit {
  marker: string;
}

export interface Estimate {
  category: string;
  model_calls: number;
  estimated_input_tokens: number;
  max_output_tokens: number;
  retrieval_operations: number;
  research_steps: number;
  question_credits: number;
  expensive: boolean;
  explanation: string | null;
  is_estimate: boolean;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  insufficient_evidence: boolean;
  status: "complete" | "error" | "streaming" | "interrupted" | string;
  error: { code: string; message: string; retryable?: boolean } | null;
  retrieval: { degraded: string[]; counts: Record<string, number>; timings_ms: Record<string, number> } | null;
  operation: { category: string; charged: boolean; model: string | null } | null;
  created_at: string | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  message_count: number;
  updated_at: string | null;
}

export interface Filters {
  source_ids: string[];
  file_ids: string[];
  collection_id: string | null;
  source_type: "book" | "document" | null;
  language: string | null;
  author: string | null;
  date_from: string | null;
  date_to: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  filters: Filters;
  messages: Message[];
}

export interface SearchResponse {
  items: Hit[];
  degraded: string[];
  counts: Record<string, number>;
  timings_ms: Record<string, number>;
}

export interface Chunk {
  id: string;
  source_id: string;
  source_type: string;
  source_title: string;
  file_id: string;
  section_path: string[];
  page_start: number | null;
  page_end: number | null;
  locations: unknown[];
  citation_label: string | null;
  ordinal: number;
  text: string;
  overlap_prefix_chars: number;
  neighbours: { id: string; ordinal: number; text: string; page_start: number | null }[];
}

export interface GraphNode {
  id: string;
  name: string;
  type: string;
  description: string;
  mention_count: number;
  source_count: number;
  provenance: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  confidence: number | null;
  confidence_is_estimate: boolean;
  evidence_count: number;
  provenance: string;
  verified: boolean;
}

export interface Subgraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  limits: { max_nodes: number; max_edges: number };
}

export interface GraphMeta {
  entity_types: string[];
  relationship_types: string[];
  entities_by_type: Record<string, number>;
  relationships: number;
  files_by_graph_status: Record<string, number>;
  disclaimer: string;
}

export interface Evidence {
  chunk_id: string;
  source_id: string;
  file_id: string;
  source_title: string;
  citation_label: string | null;
  section_path: string[];
  page_start: number | null;
  snippet: string;
}

export interface EntityDetail extends GraphNode {
  sources: { id: string; title: string; kind: string }[];
  mentions: Evidence[];
  mentions_page: number;
  relationships: (GraphEdge & { other: { id: string; name: string }; direction: "in" | "out" })[];
}

export interface RelationshipDetail extends GraphEdge {
  source_entity: GraphNode | null;
  target_entity: GraphNode | null;
  descriptions: string[];
  evidence: Evidence[];
  disclaimer: string;
}

export interface ResearchProject {
  id: string;
  title: string;
  question: string;
  status: "active" | "archived";
  run_count: number;
  last_run_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ResearchRun {
  id: string;
  project_id: string;
  question: string;
  status: "queued" | "running" | "completed" | "incomplete" | "failed" | "cancelled" | string;
  phase: string;
  depth: "quick" | "standard" | "thorough";
  budget: Record<string, number | boolean>;
  estimate: Estimate | null;
  plan: { title: string; outline: string[]; subquestions: { id: string; question: string }[] } | null;
  sources_found: number;
  gaps: string[];
  conflicts: number;
  usage: { calls: number; input_tokens: number; output_tokens: number };
  report_id: string | null;
  stop_reason: string | null;
  error: { code: string; message: string } | null;
  cancel_requested: boolean;
  job_status: string | null;
  progress: JobProgress | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface RunEvent {
  seq: number;
  type: string;
  data: Record<string, unknown>;
  at: string;
}

export interface ReportSummary {
  id: string;
  title: string;
  question: string;
  status: "complete" | "incomplete" | string;
  incomplete_reason: string | null;
  project_id: string | null;
  research_job_id: string | null;
  created_at: string | null;
  evidence_count: number;
}

export interface ReportEvidence extends Evidence {
  label: string;
  cited: boolean;
}

export interface Report extends ReportSummary {
  sections: {
    executive_summary: string;
    findings: { heading: string; body: string; kind: "supported" | "hypothesis" | string }[];
    contradictions: string[];
    cross_source_connections: string[];
    limitations: string[];
    open_questions: string[];
  };
  research_questions: string[];
  conflicts: { description: string; evidence: string[] }[];
  evidence: ReportEvidence[];
  markdown: string;
  degraded: string[];
}

export interface Finding {
  id: string;
  text: string;
  note: string;
  tags: string[];
  citations: { chunk_id: string; source_id: string; source_title: string | null; citation_label: string | null }[];
  report_id: string | null;
  created_at: string | null;
}

export interface Subscription {
  id: string;
  status: string;
  billing_interval: "month" | "year" | null;
  current_period_start: string | null;
  current_period_end: string | null;
  cancels_at_period_end: boolean;
  scheduled_change: { action: string; effective_at: string | null } | null;
  past_due_since: string | null;
  plan_version: number | null;
  unmapped_price: boolean;
}

export interface BillingResponse {
  billing_enabled: boolean;
  environment: string | null;
  client_token: string | null;
  entitlements: EntitlementsOut;
  subscription: Subscription | null;
  has_billing_portal: boolean;
  history: { action: string; at: string; details: Record<string, unknown> }[];
  note: string;
}

export interface SessionItem {
  id: string;
  current: boolean;
  created_at: string;
  last_seen_at: string;
  user_agent: string | null;
  network: string | null;
}

export interface SavedSearch {
  id: string;
  name: string;
  query: string;
  filters: Filters;
  created_at: string;
}
