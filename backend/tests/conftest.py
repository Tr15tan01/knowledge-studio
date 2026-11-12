"""Test harness.

Tests run against a real MongoDB server (a local replica set or an Atlas test cluster)
given by TEST_MONGODB_URI. Each test session uses a fresh, randomly named database that is
dropped afterwards. External AI and billing providers are replaced by deterministic fakes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

_TMP = tempfile.mkdtemp(prefix="ks-test-")
os.environ.update(
    {
        "APP_ENV": "test",
        "MONGODB_URI": os.environ.get("TEST_MONGODB_URI", "mongodb://127.0.0.1:27017/?replicaSet=rs0"),
        "MONGODB_DATABASE": f"ks_test_{uuid.uuid4().hex[:10]}",
        "SESSION_SECRET": "test-secret-" + "x" * 40,
        "LOCAL_STORAGE_DIR": os.path.join(_TMP, "storage"),
        "STORAGE_BACKEND": "local",
        # Most tests exercise originals (download, export, reprocessing); deleting them
        # after indexing is covered in test_settings_storage.py.
        "KEEP_ORIGINAL_FILES": "1",
        "VECTOR_SEARCH_BACKEND": "exact",
        "KEYWORD_SEARCH_BACKEND": "text_index",
        "EMBEDDING_PROVIDER": "gemini",
        "GEMINI_API_KEY": "test-not-used",
        "GEMINI_EMBEDDING_DIMENSIONS": "128",
        "PADDLE_ENABLED": "1",
        "PADDLE_WEBHOOK_SECRET": "pdl_ntfset_test_secret",
        "PADDLE_API_KEY": "",
        "PADDLE_WEBHOOK_TOLERANCE_SECONDS": "5",
        "RATE_LIMIT_ENABLED": "1",
        "EMAIL_PROVIDER": "none",
        "LOG_JSON": "0",
        "LOG_LEVEL": "WARNING",
        "APP_PUBLIC_URL": "http://testserver",
        "CORS_ALLOWED_ORIGINS": "http://testserver",
    }
)

import httpx  # noqa: E402

from app.core.config import get_settings, reset_settings_cache  # noqa: E402

reset_settings_cache()


@pytest.fixture(scope="session")
async def app() -> AsyncIterator[Any]:
    from app.billing.catalog import seed_catalog
    from app.database.client import mongo
    from app.database.setup import setup_database
    from app.main import create_app

    s = get_settings()
    await mongo.connect(s)
    await setup_database(mongo.db)
    await seed_catalog()
    from tests import fakes

    fakes.install()
    application = create_app()
    yield application
    await mongo.client.drop_database(s.mongodb_database)
    await mongo.close()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
async def clean_db(app: Any) -> AsyncIterator[None]:
    yield
    from app.database.client import mongo

    for name in await mongo.db.list_collection_names():
        if name in ("plans", "plan_versions"):
            continue
        await mongo.db[name].delete_many({})
    shutil.rmtree(os.path.join(_TMP, "storage"), ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "storage"), exist_ok=True)
    # Restore catalog to the seeded state for isolation.
    await mongo.db.plans.delete_many({})
    await mongo.db.plan_versions.delete_many({})
    from app.services.system_settings import reset_runtime_settings_cache

    reset_runtime_settings_cache()
    from app.billing.catalog import seed_catalog

    await seed_catalog()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


class Session:
    """A signed-in test client that sends the CSRF header automatically."""

    def __init__(self, client: httpx.AsyncClient, user: dict[str, Any]) -> None:
        self.c = client
        self.user = user

    @property
    def csrf(self) -> str:
        return self.c.cookies.get("ks_csrf") or ""

    def _h(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {"x-csrf-token": self.csrf, **(extra or {})}

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.c.get(url, **kw)

    async def post(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> httpx.Response:
        return await self.c.post(url, headers=self._h(headers), **kw)

    async def patch(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> httpx.Response:
        return await self.c.patch(url, headers=self._h(headers), **kw)

    async def put(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> httpx.Response:
        return await self.c.put(url, headers=self._h(headers), **kw)

    async def delete(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> httpx.Response:
        return await self.c.request("DELETE", url, headers=self._h(headers), **kw)


async def make_session(app: Any, email: str | None = None, password: str = "correct-horse-battery", role: str = "USER") -> Session:
    transport = httpx.ASGITransport(app=app, client=(f"10.0.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}", 50000))
    c = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = await c.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    user = r.json()["user"]
    if role != "USER":
        from bson import ObjectId

        from app.database.client import mongo

        await mongo.db.users.update_one({"_id": ObjectId(user["id"])}, {"$set": {"role": role}})
        user["role"] = role
    return Session(c, user)


@pytest.fixture
async def user(app: Any) -> AsyncIterator[Session]:
    s = await make_session(app)
    yield s
    await s.c.aclose()


@pytest.fixture
async def other_user(app: Any) -> AsyncIterator[Session]:
    s = await make_session(app)
    yield s
    await s.c.aclose()


@pytest.fixture
async def admin(app: Any) -> AsyncIterator[Session]:
    s = await make_session(app, role="ADMIN")
    yield s
    await s.c.aclose()


@pytest.fixture
async def super_admin(app: Any) -> AsyncIterator[Session]:
    s = await make_session(app, role="SUPER_ADMIN")
    yield s
    await s.c.aclose()
