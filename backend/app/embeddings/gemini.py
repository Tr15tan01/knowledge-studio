"""Google Gemini embeddings via the official Google Gen AI SDK.

``gemini-embedding-2`` does not accept ``task_type``; retrieval uses text prefixes instead:
documents ``title: {title} | text: {content}`` and queries
``task: search result | query: {content}`` (see app/documents/chunking.py). Non-3072
dimensional outputs are normalised by the API.

Multiple strings passed directly as ``contents`` are aggregated into ONE embedding by this
model, so each input is wrapped in its own ``Content`` object to get one vector per chunk.
Vector count and dimensionality are validated before anything is stored.
"""

from __future__ import annotations

import asyncio
import time

from app.core.config import Settings
from app.core.errors import ProviderError
from app.documents.chunking import estimate_tokens, query_text
from app.embeddings.base import EmbeddingModelMetadata, EmbeddingProvider, EmbeddingResult, validate_vectors
from app.providers import telemetry
from app.providers.resilience import CircuitBreaker, RateLimiter, TransientError, with_retries

MAX_INPUT_TOKENS = 8192


class GeminiEmbeddings(EmbeddingProvider):
    def __init__(self, s: Settings) -> None:
        self.model = s.gemini_embedding_model
        self.dims = s.gemini_embedding_dimensions
        self.space_id = s.embedding_space_id
        self._api_key = s.gemini_api_key.get_secret_value()
        self._client = None
        self.batch_size = max(1, min(100, s.embedding_batch_size))
        self._sem = asyncio.Semaphore(max(1, s.embedding_max_concurrency))
        self._limiter = RateLimiter(s.embedding_requests_per_minute)
        self._breaker = CircuitBreaker("Gemini embedding")
        self._timeout = s.llm_timeout_seconds

    def _sdk(self):  # type: ignore[no-untyped-def]
        if not self._api_key:
            raise ProviderError("GEMINI_API_KEY is not configured.", code="provider_not_configured", status_code=503)
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def get_model_metadata(self) -> EmbeddingModelMetadata:
        return EmbeddingModelMetadata(
            provider="gemini",
            model=self.model,
            dimensions=self.dims,
            space_id=self.space_id,
            max_input_tokens=MAX_INPUT_TOKENS,
            document_format="title: {title} | text: {content}",
            query_format="task: search result | query: {content}",
        )

    async def _call(self, texts: list[str]) -> list[list[float]]:
        from google.genai import errors, types

        client = self._sdk()

        async def once() -> list[list[float]]:
            await self._limiter.acquire()
            try:
                resp = await client.aio.models.embed_content(
                    model=self.model,
                    contents=[types.Content(parts=[types.Part(text=t)]) for t in texts],
                    config=types.EmbedContentConfig(output_dimensionality=self.dims),
                )
            except errors.APIError as exc:
                code = getattr(exc, "code", None) or 0
                if code == 429 or code >= 500:
                    raise TransientError(f"gemini {code}") from exc
                if code in (401, 403):
                    raise ProviderError(
                        "The embedding provider rejected the API key.", code="provider_auth_failed", status_code=503
                    ) from exc
                if code == 404:
                    raise ProviderError(
                        f"Embedding model '{self.model}' was not found.", code="provider_model_not_found", status_code=503
                    ) from exc
                raise ProviderError("The embedding request was rejected by the provider.", code="provider_rejected") from exc
            except (ConnectionError, OSError) as exc:
                raise TransientError(type(exc).__name__) from exc
            return [list(e.values or []) for e in (resp.embeddings or [])]

        return await with_retries(once, breaker=self._breaker, timeout=self._timeout)

    async def embed_documents(self, texts: list[str], *, account_id: object = None, job_id: object = None) -> EmbeddingResult:
        vectors: list[list[float]] = []
        tokens = 0
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            for t in batch:
                if estimate_tokens(t) > MAX_INPUT_TOKENS:
                    raise ProviderError("A chunk exceeds the embedding model's input limit.", code="embedding_input_too_long")
            t0 = time.perf_counter()
            async with self._sem:
                try:
                    out = await self._call(batch)
                except ProviderError as exc:
                    await telemetry.record_call(
                        provider="gemini",
                        model=self.model,
                        operation="embed_documents",
                        kind="embed",
                        account_id=account_id,
                        job_id=job_id,
                        status="error",
                        error_code=exc.code,
                        latency_ms=(time.perf_counter() - t0) * 1000,
                    )
                    raise
            validate_vectors(out, len(batch), self.dims)
            batch_tokens = sum(estimate_tokens(t) for t in batch)
            tokens += batch_tokens
            await telemetry.record_call(
                provider="gemini",
                model=self.model,
                operation="embed_documents",
                kind="embed",
                account_id=account_id,
                job_id=job_id,
                input_tokens=batch_tokens,
                latency_ms=(time.perf_counter() - t0) * 1000,
                tokens_estimated=True,
            )
            vectors.extend(out)
        return EmbeddingResult(vectors=vectors, input_tokens=tokens, tokens_estimated=True)

    async def embed_query(self, query: str, *, account_id: object = None) -> list[float]:
        text = query_text(query)
        t0 = time.perf_counter()
        out = await self._call([text])
        validate_vectors(out, 1, self.dims)
        await telemetry.record_call(
            provider="gemini",
            model=self.model,
            operation="embed_query",
            kind="embed",
            account_id=account_id,
            input_tokens=estimate_tokens(text),
            latency_ms=(time.perf_counter() - t0) * 1000,
            tokens_estimated=True,
        )
        return out[0]

    async def validate_configuration(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "GEMINI_API_KEY missing"
        try:
            v = await self._call(["task: search result | query: health check"])
            validate_vectors(v, 1, self.dims)
            return True, f"{self.model} ({self.dims} dims) reachable"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}"
