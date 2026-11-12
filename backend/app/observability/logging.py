"""Structured JSON logging with request/job correlation and secret redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
job_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_id", default=None)
user_ref_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_ref", default=None)

_SENSITIVE_KEYS = re.compile(r"(password|passwd|secret|token|api[_-]?key|authorization|cookie|session|signature|credential)", re.I)
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}
_MAX_FIELD = 2000


def redact(value: Any, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str) and len(value) > _MAX_FIELD:
        return value[:_MAX_FIELD] + "…[truncated]"
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        if rid := request_id_var.get():
            payload["request_id"] = rid
        if jid := job_id_var.get():
            payload["job_id"] = jid
        if uref := user_ref_var.get():
            payload["user"] = uref
        for k, v in record.__dict__.items():
            if k in _RESERVED or k.startswith("_"):
                continue
            payload[k] = redact(v, k)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = self.formatException(record.exc_info)[-4000:]
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = {k: redact(v, k) for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        base = f"{datetime.fromtimestamp(record.created).strftime('%H:%M:%S')} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if rid := request_id_var.get():
            extras["request_id"] = rid
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_logs else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("pymongo", "httpx", "httpcore", "google_genai", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
