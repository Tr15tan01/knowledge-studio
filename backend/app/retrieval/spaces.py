"""Embedding-space bookkeeping.

Each vector field has a companion marker field recording which embedding space produced
the vector: ``embedding`` ↔ ``embedding_space`` (default) and e.g. ``embedding_v2`` ↔
``embedding_v2_space``. Retrieval always filters on the active field's marker, so vectors
from different models/dimensions are never compared.
"""

from __future__ import annotations

from app.core.config import get_settings


def marker_for(vector_field: str) -> str:
    return "embedding_space" if vector_field == "embedding" else f"{vector_field}_space"


def active_vector_field() -> str:
    return get_settings().vector_field


def active_marker() -> str:
    return marker_for(active_vector_field())


def active_space() -> str:
    return get_settings().embedding_space_id
