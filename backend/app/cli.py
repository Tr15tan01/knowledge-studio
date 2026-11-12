"""Operational command-line interface.

    python -m app.cli setup-db                 # collections, validators, indexes, plan seed
    python -m app.cli setup-search-indexes     # Atlas Vector Search + Atlas Search indexes
    python -m app.cli check-config             # validate environment configuration
    python -m app.cli create-admin --email you@example.com --role SUPER_ADMIN
    python -m app.cli demote-admin --email someone@example.com
    python -m app.cli reconcile                # storage + usage reconciliation now

The CLI never prints passwords, tokens or secret values.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from app.core.config import get_settings
from app.database.client import mongo


async def _connect() -> None:
    await mongo.connect(get_settings())


async def cmd_setup_db(_: argparse.Namespace) -> int:
    from app.billing.catalog import seed_catalog
    from app.database.setup import setup_database

    await _connect()
    await setup_database(mongo.db)
    from app.billing.catalog import bootstrap_price_mappings

    created = await seed_catalog()
    print(f"Database '{get_settings().mongodb_database}' ready. Transactions supported: {mongo.transactions_supported}.")
    print(f"Seeded plans: {len(created)} (0 means the catalog already existed).")
    for interval, status in (await bootstrap_price_mappings()).items():
        print(f"Premium {interval} price from environment: {status}")
    return 0


async def cmd_setup_search(args: argparse.Namespace) -> int:
    from app.retrieval.atlas_indexes import ensure_search_indexes

    await _connect()
    result = await ensure_search_indexes(mongo.db, wait=args.wait)
    for name, status in result.items():
        print(f"{name}: {status}")
    return (
        0 if all(v in ("READY", "created", "exists") or str(v).startswith(("READY", "PENDING", "BUILDING")) for v in result.values()) else 1
    )


async def cmd_check_config(_: argparse.Namespace) -> int:
    s = get_settings()
    problems = s.validate_for_startup()
    print(f"Environment: {s.app_env.value}")
    for k, ok in _config_flags().items():
        print(f"  {k:<28} {'configured' if ok else 'missing'}")
    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nConfiguration OK.")
    return 0


def _config_flags() -> dict[str, bool]:
    s = get_settings()
    return {
        "MONGODB_URI": bool(s.mongodb_uri.get_secret_value()),
        "SESSION_SECRET": len(s.session_secret.get_secret_value()) >= 32,
        "GEMINI_API_KEY": bool(s.gemini_api_key.get_secret_value()),
        "PADDLE_API_KEY": bool(s.paddle_api_key.get_secret_value()),
        "PADDLE_WEBHOOK_SECRET": bool(s.paddle_webhook_secret.get_secret_value()),
        "GOOGLE_CLIENT_SECRET": bool(s.google_client_secret.get_secret_value()),
        "SMTP_PASSWORD": bool(s.smtp_password.get_secret_value()),
        "S3 credentials (optional)": bool(s.s3_access_key_id.get_secret_value()),
    }


async def cmd_create_admin(args: argparse.Namespace) -> int:
    from app.auth.service import normalize_email
    from app.core.timeutil import utcnow
    from app.services import audit

    await _connect()
    email = normalize_email(args.email)
    user: dict[str, Any] | None = await mongo.db.users.find_one({"email_normalized": email})
    if not user:
        print("No account exists for that email. Register it through the application first, then re-run this command.")
        print("(See docs/admin.md: no default administrator with known credentials is ever created.)")
        return 2
    if user["status"] != "active":
        print(f"Account status is '{user['status']}'; only active accounts can be promoted.")
        return 2
    if not user.get("email_verified_at") and not args.allow_unverified:
        print(
            "Account email is not verified. Verify it first, or pass --allow-unverified if email verification is disabled in this deployment."
        )
        return 2
    if user.get("role") == args.role:
        print(f"Account already has role {args.role}. Nothing to do.")
        return 0
    if not args.yes:
        typed = input(f"Type the email address to confirm promoting it to {args.role}: ").strip().lower()
        if typed != email:
            print("Confirmation did not match. Aborted.")
            return 3
    await mongo.db.users.update_one({"_id": user["_id"]}, {"$set": {"role": args.role, "updated_at": utcnow()}})
    await audit.record(
        "admin.role_granted",
        actor_type="cli",
        target_type="user",
        target_id=user["_id"],
        reason=args.reason or "bootstrap via CLI",
        details={"from": user.get("role"), "to": args.role},
    )
    print(f"Promoted account to {args.role}. Sign out and back in to refresh the session.")
    return 0


async def cmd_demote_admin(args: argparse.Namespace) -> int:
    from app.auth.service import normalize_email
    from app.services import audit

    await _connect()
    user = await mongo.db.users.find_one({"email_normalized": normalize_email(args.email)})
    if not user or user.get("role") == "USER":
        print("No administrator account found for that email.")
        return 2
    if user["role"] == "SUPER_ADMIN":
        others = await mongo.db.users.count_documents({"role": "SUPER_ADMIN", "status": "active", "_id": {"$ne": user["_id"]}})
        if others == 0:
            print("Refusing: this is the last active SUPER_ADMIN.")
            return 3
    await mongo.db.users.update_one({"_id": user["_id"]}, {"$set": {"role": "USER"}})
    await audit.record("admin.role_revoked", actor_type="cli", target_type="user", target_id=user["_id"], details={"from": user["role"]})
    print("Administrator role removed.")
    return 0


async def cmd_reconcile(_: argparse.Namespace) -> int:
    from app.billing.storage_accounting import reconcile_storage_objects
    from app.billing.usage import expire_stale_reservations

    await _connect()
    released = await expire_stale_reservations()
    report = await reconcile_storage_objects()
    print(f"Expired reservations released: {released}")
    for k, v in report.items():
        print(f"{k}: {v}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup-db")
    ss = sub.add_parser("setup-search-indexes")
    ss.add_argument("--wait", action="store_true", help="wait until indexes are queryable")
    sub.add_parser("check-config")
    ca = sub.add_parser("create-admin")
    ca.add_argument("--email", required=True)
    ca.add_argument("--role", choices=["ADMIN", "SUPER_ADMIN"], default="ADMIN")
    ca.add_argument("--reason", default="")
    ca.add_argument("--allow-unverified", action="store_true")
    ca.add_argument("--yes", action="store_true", help="skip the interactive confirmation (for automation)")
    da = sub.add_parser("demote-admin")
    da.add_argument("--email", required=True)
    sub.add_parser("reconcile")
    args = p.parse_args(argv)
    handlers = {
        "setup-db": cmd_setup_db,
        "setup-search-indexes": cmd_setup_search,
        "check-config": cmd_check_config,
        "create-admin": cmd_create_admin,
        "demote-admin": cmd_demote_admin,
        "reconcile": cmd_reconcile,
    }

    async def run() -> int:
        try:
            return await handlers[args.cmd](args)
        finally:
            await mongo.close()

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
