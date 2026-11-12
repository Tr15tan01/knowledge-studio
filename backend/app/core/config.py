"""Typed application settings.

All configuration comes from environment variables (optionally a `.env` file in
development). Secrets are never stored in the database; environment variables are
authoritative in every environment.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # `KEY=` lines (as shipped in .env.example) mean "not set": use the default.
        env_ignore_empty=True,
    )

    # --- Application -----------------------------------------------------------
    app_env: Environment = Environment.DEVELOPMENT
    app_name: str = "Knowledge Studio"
    app_public_url: str = "http://localhost:3000"
    api_public_url: str = "http://localhost:8000"
    cors_allowed_origins: str = "http://localhost:3000"
    log_level: str = "INFO"
    log_json: bool = True

    # --- MongoDB Atlas -----------------------------------------------------------
    mongodb_uri: SecretStr = SecretStr("")
    mongodb_database: str = "knowledge_studio"
    mongodb_server_selection_timeout_ms: int = 8000

    # --- Sessions & security -----------------------------------------------------
    registration_enabled: bool = True  # default; administrators can close sign-ups in Admin > Settings
    session_secret: SecretStr = SecretStr("")
    session_cookie_name: str = "ks_session"
    csrf_cookie_name: str = "ks_csrf"
    session_ttl_hours: int = 24 * 14
    session_idle_timeout_hours: int = 24 * 3
    session_rotate_after_minutes: int = 60 * 12
    reauth_window_minutes: int = 15
    cookie_secure: bool | None = None  # defaults to True outside development/test
    cookie_domain: str | None = None
    email_verification_required: bool = False
    password_min_length: int = 10

    # --- Google OAuth ------------------------------------------------------------
    google_auth_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = ""

    # --- Email -------------------------------------------------------------------
    email_provider: Literal["none", "smtp", "console"] = "none"
    email_from: str = "Knowledge Studio <no-reply@localhost>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_starttls: bool = True

    # --- File storage ------------------------------------------------------------
    # Defaults only: administrators choose the backend and whether originals are kept in
    # Admin > Settings (app.services.system_settings.RuntimeSettings).
    storage_backend: Literal["local", "mongodb", "s3"] = "local"
    keep_original_files: bool = False
    failed_original_retention_days: int = 7
    local_storage_dir: str = "./var/storage"
    s3_bucket: str = ""
    s3_region: str = ""
    s3_endpoint_url: str = ""
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    signed_url_ttl_seconds: int = 300
    upload_hard_max_bytes: int = 200 * 1024 * 1024  # platform ceiling regardless of plan

    # --- Embeddings --------------------------------------------------------------
    embedding_provider: Literal["gemini", "none"] = "gemini"
    gemini_embedding_model: str = "gemini-embedding-2"
    gemini_embedding_dimensions: int = 768
    gemini_api_key: SecretStr = SecretStr("")
    embedding_space_id: str = ""  # derived when empty
    embedding_batch_size: int = 32
    embedding_max_concurrency: int = 2
    embedding_requests_per_minute: int = 300

    # --- Generative providers ----------------------------------------------------
    llm_provider: Literal["gemini", "none"] = "gemini"
    llm_model: str = "gemini-3.8-flash"
    llm_api_key: SecretStr = SecretStr("")  # falls back to GEMINI_API_KEY
    research_llm_provider: Literal["gemini", "none"] = "gemini"
    research_llm_model: str = ""
    extraction_llm_provider: Literal["gemini", "none"] = "gemini"
    extraction_llm_model: str = ""
    reranker_provider: Literal["none"] = "none"
    reranker_model: str = ""
    llm_timeout_seconds: float = 90.0
    llm_max_concurrency: int = 4

    # --- Retrieval ---------------------------------------------------------------
    vector_search_backend: Literal["atlas", "exact"] = "atlas"
    keyword_search_backend: Literal["atlas", "text_index"] = "atlas"
    atlas_vector_index_name: str = "chunks_vector_v1"
    atlas_search_index_name: str = "chunks_text_v1"
    vector_field: str = "embedding"
    retrieval_top_k: int = 8
    retrieval_candidates: int = 40
    context_max_tokens: int = 12000
    chunk_target_tokens: int = 800
    chunk_overlap_tokens: int = 120
    chunking_version: str = "struct-v1"
    max_chunks_per_ingestion: int = 20000
    ocr_provider: Literal["none"] = "none"

    # --- Paddle ------------------------------------------------------------------
    paddle_enabled: bool = False
    paddle_environment: Literal["sandbox", "production"] = "sandbox"
    paddle_api_key: SecretStr = SecretStr("")
    paddle_client_token: str = ""
    paddle_webhook_secret: SecretStr = SecretStr("")
    paddle_webhook_tolerance_seconds: int = 5
    paddle_price_id_premium_monthly: str = ""
    paddle_price_id_premium_yearly: str = ""
    billing_past_due_grace_days: int = 3

    # --- Jobs --------------------------------------------------------------------
    job_lease_seconds: int = 120
    job_heartbeat_seconds: int = 30
    job_max_attempts: int = 5
    job_poll_interval_seconds: float = 1.0
    worker_concurrency: int = 2
    # Run the background worker inside the API process, for deployments with a single
    # service and no separate worker. Start the API with one uvicorn worker.
    run_worker_in_api: bool = False
    queue_backend: Literal["mongodb"] = "mongodb"

    # --- Rate limits -------------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_login_per_15m: int = 10
    rate_limit_register_per_hour: int = 10
    rate_limit_password_reset_per_hour: int = 5
    rate_limit_ai_per_minute: int = 20
    rate_limit_upload_per_minute: int = 10

    # --- Platform-wide AI cost safety ---------------------------------------------
    platform_monthly_ai_budget_usd: float = 200.0

    # --- Exports -------------------------------------------------------------------
    pdf_font_path: str = ""

    # --- Monitoring --------------------------------------------------------------
    sentry_dsn: SecretStr = SecretStr("")
    sentry_traces_sample_rate: float = 0.0

    # --- Feature flags -----------------------------------------------------------
    feature_web_research: bool = False
    feature_graph_extraction: bool = True

    @field_validator("gemini_embedding_dimensions")
    @classmethod
    def _dims_range(cls, v: int) -> int:
        if not 128 <= v <= 3072:
            raise ValueError("GEMINI_EMBEDDING_DIMENSIONS must be between 128 and 3072")
        return v

    @model_validator(mode="after")
    def _derive(self) -> Settings:
        if self.cookie_secure is None:
            self.cookie_secure = self.app_env not in (Environment.DEVELOPMENT, Environment.TEST)
        if not self.research_llm_model:
            self.research_llm_model = self.llm_model
        if not self.extraction_llm_model:
            self.extraction_llm_model = self.llm_model
        if not self.embedding_space_id:
            self.embedding_space_id = f"{self.embedding_provider}:{self.gemini_embedding_model}:{self.gemini_embedding_dimensions}"
        return self

    # ------------------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.app_env in (Environment.PRODUCTION, Environment.STAGING)

    @property
    def effective_llm_api_key(self) -> str:
        return self.llm_api_key.get_secret_value() or self.gemini_api_key.get_secret_value()

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    def validate_for_startup(self) -> list[str]:
        """Return a list of fatal configuration problems.

        Production refuses to start with missing secrets; development reports problems
        clearly instead of substituting insecure defaults.
        """
        problems: list[str] = []
        if not self.mongodb_uri.get_secret_value():
            problems.append("MONGODB_URI is required.")
        secret = self.session_secret.get_secret_value()
        if len(secret) < 32:
            problems.append("SESSION_SECRET must be at least 32 characters (use scripts to generate one).")
        if self.is_production:
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be enabled in production.")
            if self.vector_search_backend != "atlas":
                problems.append("VECTOR_SEARCH_BACKEND=exact is a development fallback and is not allowed in production.")
            if self.keyword_search_backend != "atlas":
                problems.append("KEYWORD_SEARCH_BACKEND=text_index is a development fallback and is not allowed in production.")
            if self.paddle_enabled and not self.paddle_webhook_secret.get_secret_value():
                problems.append("PADDLE_WEBHOOK_SECRET is required when PADDLE_ENABLED=1.")
            if self.paddle_enabled and not self.paddle_api_key.get_secret_value():
                problems.append("PADDLE_API_KEY is required when PADDLE_ENABLED=1.")
        if self.google_auth_enabled and not (
            self.google_client_id and self.google_client_secret.get_secret_value() and self.google_redirect_uri
        ):
            problems.append("GOOGLE_AUTH_ENABLED=1 requires GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI.")
        if self.storage_backend == "s3" and not self.s3_bucket:
            problems.append("STORAGE_BACKEND=s3 requires S3_BUCKET.")
        if self.email_provider == "smtp" and not self.smtp_host:
            problems.append("EMAIL_PROVIDER=smtp requires SMTP_HOST.")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
