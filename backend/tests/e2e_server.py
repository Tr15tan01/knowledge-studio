"""Test-only server for end-to-end (Playwright) runs.

Runs the real FastAPI app and a real background worker in one process, with external AI
providers replaced by the deterministic fakes used in the pytest suite. It refuses to start
unless APP_ENV=test, so these fakes can never serve a production or development request.

    $env:APP_ENV="test"; python -m tests.e2e_server --port 8000

Everything else (MongoDB, auth, quotas, job queue, storage, citations) is the real code path.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


async def _main(port: int) -> int:
    if os.environ.get("APP_ENV") != "test":
        print("Refusing to start: tests.e2e_server requires APP_ENV=test.", file=sys.stderr)
        return 2
    os.environ.setdefault("VECTOR_SEARCH_BACKEND", "exact")
    os.environ.setdefault("KEYWORD_SEARCH_BACKEND", "text_index")
    os.environ.setdefault("GEMINI_EMBEDDING_DIMENSIONS", "128")
    os.environ.setdefault("GEMINI_API_KEY", "e2e-not-used")

    import uvicorn

    from app.billing.catalog import seed_catalog
    from app.core.config import get_settings, reset_settings_cache
    from app.database.client import mongo
    from app.database.setup import setup_database
    from app.jobs.runtime import Worker
    from app.main import create_app
    from tests import fakes

    reset_settings_cache()
    s = get_settings()
    await mongo.connect(s)
    await setup_database(mongo.db)
    await seed_catalog()
    fakes.install()

    app = create_app()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    )
    worker = Worker(concurrency=2)
    try:
        await asyncio.gather(server.serve(), worker.run())
    finally:
        worker.stop()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    sys.exit(asyncio.run(_main(ap.parse_args().port)))
