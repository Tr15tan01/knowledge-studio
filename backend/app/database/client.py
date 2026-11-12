"""MongoDB connection lifecycle using PyMongo's native asynchronous API."""

from __future__ import annotations

import logging
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.core.config import Settings
from app.core.errors import ServiceUnavailable

log = logging.getLogger("app.database")


class Mongo:
    """Holds the process-wide client. One instance per process (API or worker)."""

    def __init__(self) -> None:
        self._client: AsyncMongoClient[dict[str, Any]] | None = None
        self._db: AsyncDatabase[dict[str, Any]] | None = None
        self.transactions_supported: bool = False

    async def connect(self, settings: Settings) -> None:
        uri = settings.mongodb_uri.get_secret_value()
        if not uri:
            raise ServiceUnavailable("MONGODB_URI is not configured.")
        self._client = AsyncMongoClient(
            uri,
            tz_aware=True,
            serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
            appname="knowledge-studio",
            retryWrites=True,
        )
        self._db = self._client[settings.mongodb_database]
        # Transactions need a replica set / Atlas cluster. Detect, and only use them when
        # explicitly enabled; all critical paths have transaction-free atomic designs.
        try:
            hello = await self._client.admin.command("hello")
            self.transactions_supported = bool(hello.get("setName") or hello.get("msg") == "isdbgrid")
        except PyMongoError as exc:
            log.warning("mongo_hello_failed", extra={"error": type(exc).__name__})
            self.transactions_supported = False

    @property
    def client(self) -> AsyncMongoClient[dict[str, Any]]:
        if self._client is None:
            raise ServiceUnavailable("Database is not connected.")
        return self._client

    @property
    def db(self) -> AsyncDatabase[dict[str, Any]]:
        if self._db is None:
            raise ServiceUnavailable("Database is not connected.")
        return self._db

    def use_database(self, name: str) -> None:
        """Test helper: switch to an isolated database."""
        self._db = self.client[name]

    async def ping(self) -> bool:
        try:
            await self.client.admin.command("ping")
            return True
        except Exception:  # noqa: BLE001 - health check must not raise
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
        self._client = None
        self._db = None


mongo = Mongo()


def get_db() -> AsyncDatabase[dict[str, Any]]:
    return mongo.db
