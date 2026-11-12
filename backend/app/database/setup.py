"""Collection schema validation and index setup (idempotent).

Run with ``python -m app.cli setup-db``. Atlas Search / Vector Search indexes are
created separately by ``python -m app.cli setup-search-indexes`` because they
require an Atlas cluster (see docs/setup-atlas.md).
"""

from __future__ import annotations

import logging
from typing import Any

from pymongo import ASCENDING, DESCENDING, TEXT, IndexModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid, OperationFailure

log = logging.getLogger("app.database.setup")

A, D = ASCENDING, DESCENDING


def _idx(keys: list[tuple[str, Any]], **kw: Any) -> IndexModel:
    return IndexModel(keys, **kw)


INDEXES: dict[str, list[IndexModel]] = {
    "users": [
        _idx([("email_normalized", A)], unique=True, name="uniq_email"),
        _idx([("role", A)], name="role"),
        _idx([("created_at", D)], name="created"),
        _idx([("status", A), ("created_at", D)], name="status_created"),
    ],
    "sessions": [
        _idx([("token_hash", A)], unique=True, name="uniq_token_hash"),
        _idx([("user_id", A), ("revoked_at", A)], name="user_sessions"),
        _idx([("expires_at", A)], expireAfterSeconds=0, name="ttl_expires"),
    ],
    "oauth_accounts": [
        _idx([("provider", A), ("subject", A)], unique=True, name="uniq_provider_subject"),
        _idx([("user_id", A)], name="user"),
    ],
    "oauth_states": [
        _idx([("state_hash", A)], unique=True, name="uniq_state"),
        _idx([("expires_at", A)], expireAfterSeconds=0, name="ttl_expires"),
    ],
    "email_verification_tokens": [
        _idx([("token_hash", A)], unique=True, name="uniq_token_hash"),
        _idx([("expires_at", A)], expireAfterSeconds=86400, name="ttl_expires"),
    ],
    "password_reset_tokens": [
        _idx([("token_hash", A)], unique=True, name="uniq_token_hash"),
        _idx([("expires_at", A)], expireAfterSeconds=86400, name="ttl_expires"),
    ],
    "workspaces": [_idx([("owner_id", A), ("created_at", A)], name="owner")],
    "workspace_memberships": [
        _idx([("workspace_id", A), ("user_id", A)], unique=True, name="uniq_member"),
        _idx([("user_id", A)], name="user"),
    ],
    "files": [
        _idx([("storage_key", A)], unique=True, name="uniq_storage_key"),
        _idx([("workspace_id", A), ("created_at", D)], name="ws_created"),
        _idx([("owner_id", A), ("storage_status", A)], name="owner_status"),
        _idx([("workspace_id", A), ("checksum_sha256", A)], name="ws_checksum"),
        _idx([("processing_status", A), ("updated_at", A)], name="processing"),
    ],
    "books": [
        _idx([("file_id", A)], unique=True, name="uniq_file"),
        _idx([("workspace_id", A), ("created_at", D)], name="ws_created"),
        _idx([("workspace_id", A), ("author", A)], name="ws_author"),
    ],
    "documents": [
        _idx([("file_id", A)], unique=True, name="uniq_file"),
        _idx([("workspace_id", A), ("created_at", D)], name="ws_created"),
    ],
    "collections": [_idx([("workspace_id", A), ("name", A)], unique=True, name="uniq_ws_name")],
    "chunks": [
        _idx([("workspace_id", A), ("file_id", A), ("ordinal", A)], name="ws_file_ordinal"),
        _idx([("file_id", A), ("embedding_space", A)], name="file_space"),
        _idx([("workspace_id", A), ("embedding_space", A)], name="ws_space"),
        # Used only when KEYWORD_SEARCH_BACKEND=text_index (non-Atlas fallback).
        _idx([("text", TEXT)], name="text_fallback", default_language="none"),
    ],
    "entities": [
        _idx([("workspace_id", A), ("normalized_name", A), ("type", A)], unique=True, name="uniq_ws_entity"),
        _idx([("workspace_id", A), ("type", A), ("mention_count", D)], name="ws_type_mentions"),
        _idx([("workspace_id", A), ("source_ids", A)], name="ws_sources"),
    ],
    "relationships": [
        _idx(
            [("workspace_id", A), ("source_entity_id", A), ("target_entity_id", A), ("type", A)],
            unique=True,
            name="uniq_edge",
        ),
        _idx([("workspace_id", A), ("target_entity_id", A)], name="ws_target"),
    ],
    "entity_mentions": [
        _idx([("workspace_id", A), ("entity_id", A)], name="ws_entity"),
        _idx([("chunk_id", A), ("entity_id", A)], unique=True, name="uniq_chunk_entity"),
    ],
    "conversations": [_idx([("workspace_id", A), ("user_id", A), ("updated_at", D)], name="ws_user_updated")],
    "messages": [_idx([("conversation_id", A), ("created_at", A)], name="conv_created")],
    "research_projects": [_idx([("workspace_id", A), ("user_id", A), ("updated_at", D)], name="ws_user")],
    "research_jobs": [
        _idx([("workspace_id", A), ("user_id", A), ("created_at", D)], name="ws_user"),
        _idx([("user_id", A), ("status", A)], name="user_status"),
    ],
    "research_findings": [_idx([("workspace_id", A), ("user_id", A), ("created_at", D)], name="ws_user")],
    "reports": [_idx([("workspace_id", A), ("user_id", A), ("created_at", D)], name="ws_user")],
    "usage_counters": [_idx([("account_id", A), ("period_key", A)], name="account_period")],
    "usage_events": [
        _idx([("account_id", A), ("metric", A), ("occurred_at", D)], name="account_metric_time"),
        _idx([("occurred_at", D)], name="time"),
        _idx([("idempotency_key", A)], unique=True, sparse=True, name="uniq_idem"),
    ],
    "usage_reservations": [
        _idx([("idempotency_key", A)], unique=True, name="uniq_idem"),
        _idx([("state", A), ("expires_at", A)], name="state_expiry"),
        _idx([("counter_id", A), ("state", A)], name="counter_state"),
    ],
    "provider_calls": [
        _idx([("occurred_at", D)], name="time"),
        _idx([("account_id", A), ("occurred_at", D)], name="account_time"),
        _idx([("provider", A), ("model", A), ("occurred_at", D)], name="provider_model_time"),
    ],
    "plans": [
        _idx([("slug", A)], unique=True, name="uniq_slug"),
        _idx([("display_order", A)], name="order"),
    ],
    "plan_versions": [_idx([("plan_id", A), ("version", A)], unique=True, name="uniq_plan_version")],
    "entitlement_grants": [
        _idx([("account_id", A), ("expires_at", A)], name="account_expiry"),
        _idx([("revoked_at", A)], name="revoked"),
    ],
    "subscriptions": [
        _idx([("paddle_subscription_id", A)], unique=True, sparse=True, name="uniq_paddle_sub"),
        _idx([("account_id", A), ("status", A)], name="account_status"),
        _idx([("plan_id", A), ("plan_version", A)], name="plan_version"),
    ],
    "billing_customers": [
        _idx([("account_id", A)], unique=True, name="uniq_account"),
        _idx([("paddle_customer_id", A)], unique=True, sparse=True, name="uniq_customer"),
    ],
    "billing_events": [
        _idx([("event_id", A)], unique=True, name="uniq_event"),
        _idx([("received_at", D)], name="received"),
        _idx([("processing_status", A), ("received_at", D)], name="status"),
    ],
    "price_mappings": [_idx([("plan_id", A), ("interval", A)], unique=True, name="uniq_plan_interval")],
    "audit_events": [
        _idx([("created_at", D)], name="created"),
        _idx([("actor_id", A), ("created_at", D)], name="actor"),
        _idx([("target_type", A), ("target_id", A), ("created_at", D)], name="target"),
        _idx([("action", A), ("created_at", D)], name="action"),
    ],
    "jobs": [
        _idx([("status", A), ("priority", D), ("run_after", A)], name="claimable"),
        _idx([("lease_expires_at", A)], name="lease"),
        _idx([("dedupe_key", A)], unique=True, partialFilterExpression={"dedupe_key": {"$type": "string"}}, name="uniq_dedupe"),
        _idx([("account_id", A), ("status", A)], name="account_status"),
        _idx([("type", A), ("status", A), ("updated_at", D)], name="type_status"),
    ],
    "job_events": [_idx([("job_id", A), ("seq", A)], unique=True, name="uniq_job_seq")],
    "idempotency_records": [
        _idx([("scope", A), ("key", A)], unique=True, name="uniq_scope_key"),
        _idx([("expires_at", A)], expireAfterSeconds=0, name="ttl_expires"),
    ],
    "rate_limits": [_idx([("expires_at", A)], expireAfterSeconds=0, name="ttl_expires")],
    "worker_heartbeats": [_idx([("expires_at", A)], expireAfterSeconds=0, name="ttl_expires")],
    "data_exports": [_idx([("user_id", A), ("expires_at", D)], name="user_expiry")],
    "webhook_rejections": [_idx([("received_at", D)], name="received")],
    "reindex_migrations": [_idx([("created_at", D)], name="created")],
    "saved_searches": [_idx([("workspace_id", A), ("user_id", A)], name="ws_user")],
    "reconciliation_reports": [_idx([("created_at", D)], name="created")],
}


VALIDATORS: dict[str, dict[str, Any]] = {
    "users": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["email", "email_normalized", "role", "status", "created_at"],
            "properties": {
                "email": {"bsonType": "string"},
                "email_normalized": {"bsonType": "string"},
                "role": {"enum": ["USER", "ADMIN", "SUPER_ADMIN"]},
                "status": {"enum": ["active", "suspended", "deleting"]},
                "password_hash": {"bsonType": ["string", "null"]},
            },
        }
    },
    "files": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["owner_id", "workspace_id", "storage_key", "storage_status", "created_at"],
            "properties": {
                "original_size_bytes": {"bsonType": ["long", "int"], "minimum": 0},
                "stored_size_bytes": {"bsonType": ["long", "int", "null"], "minimum": 0},
                "storage_status": {"enum": ["uploading", "stored", "rejected", "failed", "deleting", "deleted"]},
                "kind": {"enum": ["book", "document", None]},
            },
        }
    },
    "usage_reservations": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["account_id", "metric", "amount", "state", "idempotency_key", "created_at"],
            "properties": {
                "amount": {"bsonType": ["long", "int", "double"], "minimum": 0},
                "state": {"enum": ["applying", "pending", "committed", "released", "denied"]},
            },
        }
    },
    "plan_versions": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["plan_id", "version", "entitlements", "created_at"],
            "properties": {"version": {"bsonType": "int", "minimum": 1}},
        }
    },
    "audit_events": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["action", "created_at", "actor_type"],
            "properties": {"actor_type": {"enum": ["user", "admin", "system", "cli", "webhook"]}},
        }
    },
    "billing_events": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["event_id", "event_type", "received_at", "processing_status"],
        }
    },
}


async def ensure_collections(db: AsyncDatabase[dict[str, Any]]) -> None:
    existing = set(await db.list_collection_names())
    for name in INDEXES:
        if name not in existing:
            try:
                kwargs: dict[str, Any] = {}
                if name in VALIDATORS:
                    kwargs = {"validator": VALIDATORS[name], "validationLevel": "moderate", "validationAction": "error"}
                await db.create_collection(name, **kwargs)
            except CollectionInvalid:
                pass
        elif name in VALIDATORS:
            try:
                await db.command(
                    {"collMod": name, "validator": VALIDATORS[name], "validationLevel": "moderate", "validationAction": "error"}
                )
            except OperationFailure as exc:  # pragma: no cover - permissions on shared tiers
                log.warning("collmod_failed", extra={"collection": name, "error": str(exc)})


async def ensure_indexes(db: AsyncDatabase[dict[str, Any]]) -> None:
    for name, models in INDEXES.items():
        try:
            await db[name].create_indexes(models)
        except OperationFailure as exc:
            # An index with the same name but different options: report clearly, do not drop silently.
            log.error("index_create_failed", extra={"collection": name, "error": str(exc)})
            raise


async def setup_database(db: AsyncDatabase[dict[str, Any]]) -> None:
    await ensure_collections(db)
    await ensure_indexes(db)
