"""Background worker entry point.

    python -m workers.main                    # all job types
    python -m workers.main --types ingest.file,graph.extract --concurrency 2

Handles SIGINT/SIGTERM (Ctrl+C on Windows) gracefully: stops claiming new jobs and lets
running jobs reach a checkpoint; unfinished jobs are recovered through lease expiry.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from app.core.config import get_settings
from app.database.client import mongo
from app.jobs.runtime import Worker
from app.observability.logging import configure_logging

log = logging.getLogger("app.worker")


async def main(types: list[str] | None, concurrency: int | None) -> int:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    problems = s.validate_for_startup()
    if problems:
        for p in problems:
            log.error("configuration_problem", extra={"problem": p})
        if s.is_production or not s.mongodb_uri.get_secret_value():
            return 2
    await mongo.connect(s)
    worker = Worker(concurrency=concurrency, types=types)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except (NotImplementedError, RuntimeError):  # Windows: fall back to KeyboardInterrupt
            pass
    try:
        await worker.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        worker.stop()
    finally:
        await mongo.close()
    return 0


def cli() -> int:
    p = argparse.ArgumentParser(prog="python -m workers.main")
    p.add_argument("--types", default="", help="comma-separated job types (default: all)")
    p.add_argument("--concurrency", type=int, default=None)
    a = p.parse_args()
    types = [t.strip() for t in a.types.split(",") if t.strip()] or None
    try:
        return asyncio.run(main(types, a.concurrency))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(cli())
