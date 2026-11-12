"""Embedding provider interface."""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingModelMetadata:
    provider: str
    model: str
    dimensions: int
    space_id: str
    max_input_tokens: int
    document_format: str
    query_format: str


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    input_tokens: int
    tokens_estimated: bool


class EmbeddingProvider(abc.ABC):
    @abc.abstractmethod
    async def embed_documents(self, texts: list[str], *, account_id: object = None, job_id: object = None) -> EmbeddingResult:
        """Embed pre-formatted document texts. Must return exactly one vector per input, in order."""

    @abc.abstractmethod
    async def embed_query(self, query: str, *, account_id: object = None) -> list[float]: ...

    @abc.abstractmethod
    def get_model_metadata(self) -> EmbeddingModelMetadata: ...

    @abc.abstractmethod
    async def validate_configuration(self) -> tuple[bool, str]: ...


def validate_vectors(vectors: list[list[float]], expected_count: int, dims: int) -> None:
    if len(vectors) != expected_count:
        raise ValueError(f"embedding provider returned {len(vectors)} vectors for {expected_count} inputs")
    for v in vectors:
        if len(v) != dims:
            raise ValueError(f"embedding has {len(v)} dimensions, expected {dims}")
        if any(x != x for x in v):  # NaN check
            raise ValueError("embedding contains NaN")
