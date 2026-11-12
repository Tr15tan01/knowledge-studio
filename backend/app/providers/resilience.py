"""Shared resilience primitives for external providers: rate limiting, retries, circuit breaker."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.errors import ProviderError, ServiceUnavailable

log = logging.getLogger("app.providers")
T = TypeVar("T")


class RateLimiter:
    """Token bucket (per process)."""

    def __init__(self, per_minute: int) -> None:
        self.capacity = max(1, per_minute)
        self.tokens = float(self.capacity)
        self.rate = self.capacity / 60.0
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, n: int = 1) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                wait = (n - self.tokens) / self.rate
            await asyncio.sleep(min(wait, 5.0))


class CircuitBreaker:
    """Opens after consecutive failures; half-opens after a cool-down."""

    def __init__(self, name: str, threshold: int = 5, cooldown: float = 30.0) -> None:
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if time.monotonic() - self.opened_at >= self.cooldown else "open"

    def check(self) -> None:
        if self.state == "open":
            raise ServiceUnavailable(
                f"The {self.name} service is temporarily unavailable. Please retry shortly.", code="provider_unavailable"
            )

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            if self.opened_at is None:
                log.warning("circuit_opened", extra={"provider": self.name})
            self.opened_at = time.monotonic()


class TransientError(Exception):
    """Retryable provider failure (timeouts, 429, 5xx)."""


async def with_retries(
    fn: Callable[[], Awaitable[T]],
    *,
    breaker: CircuitBreaker,
    attempts: int = 4,
    base: float = 1.0,
    cap: float = 20.0,
    timeout: float = 60.0,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T:
    breaker.check()
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = await asyncio.wait_for(fn(), timeout)
            breaker.success()
            return result
        except (TransientError, TimeoutError) as exc:
            last = exc
            breaker.failure()
            if attempt == attempts:
                break
            if on_retry:
                on_retry(attempt, exc)
            await asyncio.sleep(random.uniform(0, min(cap, base * 2 ** (attempt - 1))))
        except ProviderError:
            breaker.failure()
            raise
    raise ProviderError(
        f"The {breaker.name} provider did not respond successfully after {attempts} attempts.",
        retryable=True,
        details={"last_error": type(last).__name__ if last else None},
    )
