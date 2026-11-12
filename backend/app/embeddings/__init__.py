from __future__ import annotations

from app.core.config import get_settings
from app.core.errors import ServiceUnavailable
from app.embeddings.base import EmbeddingProvider

_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        if s.embedding_provider == "gemini":
            from app.embeddings.gemini import GeminiEmbeddings

            _provider = GeminiEmbeddings(s)
        else:
            raise ServiceUnavailable("No embedding provider is configured (EMBEDDING_PROVIDER=none).", code="embeddings_disabled")
    return _provider


def set_embedding_provider(p: EmbeddingProvider | None) -> None:
    """Dependency-injection hook (tests, re-indexing migrations)."""
    global _provider
    _provider = p
