"""Generative model provider interface and the Gemini implementation.

Providers are selected per purpose (answers, research, extraction) from environment
configuration. Only Gemini is implemented end to end; other providers can be added by
implementing ``LLMProvider`` without touching business logic.
"""

from __future__ import annotations

import abc
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.config import get_settings
from app.core.errors import ProviderError, ServiceUnavailable
from app.documents.chunking import estimate_tokens
from app.providers import telemetry
from app.providers.resilience import CircuitBreaker, RateLimiter, TransientError, with_retries

Purpose = Literal["answer", "research", "extraction"]


@dataclass
class ChatMessage:
    role: Literal["user", "model"]
    text: str


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    finish_reason: str | None = None
    tokens_estimated: bool = False
    data: Any = None


@dataclass
class StreamChunk:
    text: str = ""
    done: bool = False
    result: LLMResult | None = None


@dataclass
class CallContext:
    account_id: Any = None
    workspace_id: Any = None
    job_id: Any = None
    operation: str = "generate"
    extra: dict[str, Any] = field(default_factory=dict)


class LLMProvider(abc.ABC):
    name: str
    model: str

    @abc.abstractmethod
    async def generate(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        max_output_tokens: int,
        temperature: float = 0.2,
        json_schema: dict[str, Any] | None = None,
        ctx: CallContext | None = None,
    ) -> LLMResult: ...

    @abc.abstractmethod
    def stream(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        max_output_tokens: int,
        temperature: float = 0.2,
        ctx: CallContext | None = None,
    ) -> AsyncIterator[StreamChunk]: ...

    async def health(self) -> tuple[bool, str]:
        return True, "not checked"


class GeminiLLM(LLMProvider):
    name = "gemini"

    def __init__(self, model: str, api_key: str, timeout: float) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._client = None
        self._breaker = CircuitBreaker(f"Gemini ({model})")
        self._limiter = RateLimiter(600)

    def _sdk(self):  # type: ignore[no-untyped-def]
        if not self._api_key:
            raise ProviderError("The AI provider API key is not configured.", code="provider_not_configured", status_code=503)
        if not self.model:
            raise ProviderError("No AI model is configured (LLM_MODEL).", code="provider_not_configured", status_code=503)
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _config(self, system: str, max_output_tokens: int, temperature: float, json_schema: dict[str, Any] | None):  # type: ignore[no-untyped-def]
        from google.genai import types

        kw: dict[str, Any] = {"system_instruction": system, "max_output_tokens": max_output_tokens, "temperature": temperature}
        if json_schema is not None:
            kw["response_mime_type"] = "application/json"
            kw["response_json_schema"] = json_schema
        return types.GenerateContentConfig(**kw)

    @staticmethod
    def _contents(messages: list[ChatMessage]):  # type: ignore[no-untyped-def]
        from google.genai import types

        return [types.Content(role=m.role, parts=[types.Part(text=m.text)]) for m in messages]

    def _map_error(self, exc: Exception) -> Exception:
        from google.genai import errors

        if isinstance(exc, errors.APIError):
            code = getattr(exc, "code", 0) or 0
            if code == 429 or code >= 500:
                return TransientError(f"gemini {code}")
            if code in (401, 403):
                return ProviderError("The AI provider rejected the API key.", code="provider_auth_failed", status_code=503)
            if code == 404:
                return ProviderError(
                    f"AI model '{self.model}' was not found or is not available.", code="provider_model_not_found", status_code=503
                )
            return ProviderError("The AI provider rejected the request.", code="provider_rejected")
        if isinstance(exc, ConnectionError | OSError):
            return TransientError(type(exc).__name__)
        return exc

    async def generate(self, *, system, messages, max_output_tokens, temperature=0.2, json_schema=None, ctx=None) -> LLMResult:  # type: ignore[no-untyped-def]
        client = self._sdk()
        ctx = ctx or CallContext()
        t0 = time.perf_counter()
        retries = 0

        def note_retry(_a: int, _e: Exception) -> None:
            nonlocal retries
            retries += 1

        async def once():  # type: ignore[no-untyped-def]
            await self._limiter.acquire()
            try:
                return await client.aio.models.generate_content(
                    model=self.model,
                    contents=self._contents(messages),
                    config=self._config(system, max_output_tokens, temperature, json_schema),
                )
            except Exception as exc:  # noqa: BLE001
                raise self._map_error(exc) from exc

        try:
            resp = await with_retries(once, breaker=self._breaker, timeout=self._timeout, on_retry=note_retry)
        except ProviderError as exc:
            await telemetry.record_call(
                provider="gemini",
                model=self.model,
                operation=ctx.operation,
                kind="generate",
                account_id=ctx.account_id,
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                status="error",
                error_code=exc.code,
                retries=retries,
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
            raise
        text = resp.text or ""
        um = resp.usage_metadata
        in_tok = int(getattr(um, "prompt_token_count", 0) or 0)
        out_tok = int((getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0))
        estimated = False
        if not in_tok:
            in_tok, estimated = estimate_tokens(system) + sum(estimate_tokens(m.text) for m in messages), True
        if not out_tok:
            out_tok = estimate_tokens(text)
        finish = None
        try:
            finish = str(resp.candidates[0].finish_reason) if resp.candidates else None
        except (AttributeError, IndexError):
            pass
        await telemetry.record_call(
            provider="gemini",
            model=self.model,
            operation=ctx.operation,
            kind="generate",
            account_id=ctx.account_id,
            workspace_id=ctx.workspace_id,
            job_id=ctx.job_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            retries=retries,
            latency_ms=(time.perf_counter() - t0) * 1000,
            tokens_estimated=estimated,
        )
        data = None
        if json_schema is not None:
            try:
                data = json.loads(text) if text else None
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    "The AI provider returned malformed structured output.", code="provider_bad_output", retryable=True
                ) from exc
        return LLMResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=self.model,
            finish_reason=finish,
            tokens_estimated=estimated,
            data=data,
        )

    async def stream(self, *, system, messages, max_output_tokens, temperature=0.2, ctx=None) -> AsyncIterator[StreamChunk]:  # type: ignore[no-untyped-def,override]
        client = self._sdk()
        ctx = ctx or CallContext()
        self._breaker.check()
        t0 = time.perf_counter()
        await self._limiter.acquire()
        parts: list[str] = []
        in_tok = out_tok = 0
        try:
            stream = await client.aio.models.generate_content_stream(
                model=self.model, contents=self._contents(messages), config=self._config(system, max_output_tokens, temperature, None)
            )
            async for chunk in stream:
                if chunk.text:
                    parts.append(chunk.text)
                    yield StreamChunk(text=chunk.text)
                um = getattr(chunk, "usage_metadata", None)
                if um is not None:
                    in_tok = int(getattr(um, "prompt_token_count", 0) or in_tok)
                    out_tok = (
                        int((getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0)) or out_tok
                    )
            self._breaker.success()
        except Exception as exc:
            self._breaker.failure()
            mapped = self._map_error(exc)
            await telemetry.record_call(
                provider="gemini",
                model=self.model,
                operation=ctx.operation,
                kind="generate",
                account_id=ctx.account_id,
                workspace_id=ctx.workspace_id,
                status="error",
                error_code=getattr(mapped, "code", type(mapped).__name__),
                latency_ms=(time.perf_counter() - t0) * 1000,
                output_tokens=estimate_tokens("".join(parts)),
            )
            if isinstance(mapped, TransientError):
                raise ProviderError(
                    "The AI provider is temporarily unavailable. Please retry.",
                    code="provider_unavailable",
                    retryable=True,
                    status_code=503,
                ) from exc
            raise mapped from exc
        text = "".join(parts)
        estimated = not in_tok
        in_tok = in_tok or estimate_tokens(system) + sum(estimate_tokens(m.text) for m in messages)
        out_tok = out_tok or estimate_tokens(text)
        await telemetry.record_call(
            provider="gemini",
            model=self.model,
            operation=ctx.operation,
            kind="generate",
            account_id=ctx.account_id,
            workspace_id=ctx.workspace_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=(time.perf_counter() - t0) * 1000,
            tokens_estimated=estimated,
        )
        yield StreamChunk(
            done=True, result=LLMResult(text=text, input_tokens=in_tok, output_tokens=out_tok, model=self.model, tokens_estimated=estimated)
        )

    async def health(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "API key missing"
        return (self._breaker.state != "open", f"circuit {self._breaker.state}")


_providers: dict[str, LLMProvider] = {}


def get_llm(purpose: Purpose = "answer") -> LLMProvider:
    if purpose in _providers:
        return _providers[purpose]
    s = get_settings()
    provider, model = {
        "answer": (s.llm_provider, s.llm_model),
        "research": (s.research_llm_provider, s.research_llm_model),
        "extraction": (s.extraction_llm_provider, s.extraction_llm_model),
    }[purpose]
    if provider == "none":
        raise ServiceUnavailable(f"No AI provider is configured for {purpose}.", code="provider_not_configured")
    if provider == "gemini":
        _providers[purpose] = GeminiLLM(model, s.effective_llm_api_key, s.llm_timeout_seconds)
        return _providers[purpose]
    raise ServiceUnavailable(f"AI provider '{provider}' is not implemented.", code="provider_not_implemented")  # pragma: no cover


def set_llm(purpose: Purpose, provider: LLMProvider | None) -> None:
    if provider is None:
        _providers.pop(purpose, None)
    else:
        _providers[purpose] = provider
