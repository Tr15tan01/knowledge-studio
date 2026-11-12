"""Opt-in smoke tests against the real Gemini API.

Skipped unless RUN_EXTERNAL_TESTS=1 and EXTERNAL_GEMINI_API_KEY is set (the regular test
harness replaces GEMINI_API_KEY with a dummy value). They use a few cheap calls.

    RUN_EXTERNAL_TESTS=1 EXTERNAL_GEMINI_API_KEY=... pytest -m external -q
"""

from __future__ import annotations

import math
import os

import pytest
from pydantic import SecretStr

KEY = os.environ.get("EXTERNAL_GEMINI_API_KEY", "")
pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(os.environ.get("RUN_EXTERNAL_TESTS") != "1" or not KEY, reason="external API tests are opt-in"),
]


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def test_gemini_embeddings_one_vector_per_text_with_configured_dimensions(app: object) -> None:
    from app.core.config import get_settings
    from app.embeddings.gemini import GeminiEmbeddings

    s = get_settings().model_copy(update={"gemini_api_key": SecretStr(KEY), "gemini_embedding_dimensions": 768})
    emb = GeminiEmbeddings(s)
    docs = [
        "title: Bees | text: Beekeepers in Racha produce chestnut honey in late summer.",
        "title: Physics | text: Gluons mediate the strong interaction between quarks.",
    ]
    res = await emb.embed_documents(docs)
    assert len(res.vectors) == 2 and all(len(v) == 768 for v in res.vectors)
    q = await emb.embed_query("Where is chestnut honey produced?")
    assert len(q) == 768
    assert _cos(q, res.vectors[0]) > _cos(q, res.vectors[1])


async def test_gemini_generation_follows_grounding_prompt(app: object) -> None:
    from app.core.config import get_settings
    from app.providers.llm import ChatMessage, GeminiLLM
    from app.retrieval.rag import build_user_message, system_prompt

    llm = GeminiLLM(get_settings().llm_model, KEY, 60)
    msg = build_user_message(
        "Where do beekeepers produce chestnut honey?",
        '<sources>\n<source id="S1" title="Notes" location="p. 1">\nBeekeepers in Racha produce chestnut honey.\n</source>\n</sources>',
    )
    out = await llm.generate(system=system_prompt(None), messages=[ChatMessage("user", msg)], max_output_tokens=200)
    assert "[S1]" in out.text
    assert out.input_tokens > 0
