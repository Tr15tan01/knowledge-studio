"""Run parsers in an isolated child process with time and memory limits.

Malformed or hostile documents can make parsers loop or allocate excessively. Each parse
runs in a fresh process (``spawn``; works on Windows and Linux) that is killed when it
exceeds the time limit. On POSIX an address-space limit is also applied.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from app.documents.parsers.base import ParsedDocument, ParseError

DEFAULT_TIMEOUT = 300.0
DEFAULT_MEMORY_MB = 2048


def _child(in_path: str, out_path: str, file_type: str, filename: str, memory_mb: int) -> None:
    if sys.platform != "win32":
        try:
            import resource

            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ImportError, ValueError, OSError):
            pass
    result: dict[str, Any]
    try:
        from app.documents.parsers.base import get_parser

        data = Path(in_path).read_bytes()
        parsed = get_parser(file_type)(data, filename)
        result = {"ok": True, "doc": parsed.to_dict()}
    except ParseError as exc:
        result = {"ok": False, "code": exc.code, "message": exc.message, "retryable": exc.retryable}
    except MemoryError:
        result = {
            "ok": False,
            "code": "resource_limit",
            "message": "The document needs more memory to process than allowed.",
            "retryable": False,
        }
    except RecursionError:
        result = {"ok": False, "code": "too_complex", "message": "The document structure is too deeply nested.", "retryable": False}
    except Exception as exc:  # noqa: BLE001
        result = {
            "ok": False,
            "code": "parse_failed",
            "message": f"The document could not be parsed ({type(exc).__name__}).",
            "retryable": False,
        }
    Path(out_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


async def parse_isolated(
    data: bytes,
    file_type: str,
    filename: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    memory_mb: int = DEFAULT_MEMORY_MB,
    isolate: bool = True,
) -> ParsedDocument:
    if not isolate:
        from app.documents.parsers.base import get_parser

        return await asyncio.to_thread(get_parser(file_type), data, filename)
    with tempfile.TemporaryDirectory(prefix="ks-parse-") as d:
        in_path, out_path = os.path.join(d, "in.bin"), os.path.join(d, "out.json")
        await asyncio.to_thread(Path(in_path).write_bytes, data)
        ctx = mp.get_context("spawn")
        proc = ctx.Process(target=_child, args=(in_path, out_path, file_type, filename, memory_mb), daemon=True)
        proc.start()
        deadline = time.monotonic() + timeout
        while proc.is_alive():
            if time.monotonic() > deadline:
                proc.kill()
                proc.join(5)
                raise ParseError("parse_timeout", "Processing this document took too long and was stopped.")
            await asyncio.sleep(0.1)
        proc.join(1)
        if not os.path.exists(out_path):
            raise ParseError("resource_limit", "The document parser stopped unexpectedly (likely a resource limit).")
        result = json.loads(await asyncio.to_thread(Path(out_path).read_text, "utf-8"))
    if not result["ok"]:
        raise ParseError(result["code"], result["message"], retryable=result.get("retryable", False))
    return ParsedDocument.from_dict(result["doc"])
