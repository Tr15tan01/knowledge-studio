"""Deterministic test doubles for external providers (installed for the whole test session)."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from app.embeddings.base import EmbeddingModelMetadata, EmbeddingProvider, EmbeddingResult

_WORD = re.compile(r"\w+", re.UNICODE)


def hashed_vector(text: str, dims: int) -> list[float]:
    """Bag-of-words hashing embedding: texts sharing words have high cosine similarity."""
    v = [0.0] * dims
    for w in _WORD.findall(text.lower()):
        if w in {"title", "text", "task", "search", "result", "query", "none", "the", "a", "of", "and", "is", "in", "to"}:
            continue
        h = int.from_bytes(hashlib.sha256(w.encode()).digest()[:8], "big")
        v[h % dims] += 1.0 if (h >> 63) & 1 else -1.0
        v[(h >> 16) % dims] += 0.5
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class FakeEmbeddings(EmbeddingProvider):
    def __init__(self, dims: int = 128, space: str | None = None) -> None:
        from app.core.config import get_settings

        self.dims = dims
        self.space = space or get_settings().embedding_space_id
        self.calls = 0
        self.fail_next: Exception | None = None

    def get_model_metadata(self) -> EmbeddingModelMetadata:
        return EmbeddingModelMetadata(
            "gemini",
            "gemini-embedding-2",
            self.dims,
            self.space,
            8192,
            "title: {title} | text: {content}",
            "task: search result | query: {content}",
        )

    async def embed_documents(self, texts: list[str], *, account_id: Any = None, job_id: Any = None) -> EmbeddingResult:
        self.calls += 1
        if self.fail_next:
            e, self.fail_next = self.fail_next, None
            raise e
        return EmbeddingResult([hashed_vector(t, self.dims) for t in texts], sum(len(t) // 4 for t in texts), True)

    async def embed_query(self, query: str, *, account_id: Any = None) -> list[float]:
        return hashed_vector(query, self.dims)

    async def validate_configuration(self) -> tuple[bool, str]:
        return True, "fake"


def install() -> None:
    from app.documents import ingest
    from app.embeddings import set_embedding_provider

    set_embedding_provider(FakeEmbeddings())
    install_llm()
    ingest.PARSE_ISOLATION = False  # parsing isolation is exercised in a dedicated test


async def drain(max_jobs: int = 200, types: list[str] | None = None) -> list[tuple[str, str]]:
    """Run queued jobs synchronously until the queue is empty (test helper)."""
    from app.jobs import queue
    from app.jobs.runtime import run_one

    results = []
    for _ in range(max_jobs):
        job = await queue.claim("test-worker", types)
        if not job:
            break
        results.append((job["type"], await run_one(job, "test-worker")))
    return results


# --- Fake generative model ---------------------------------------------------------------

from collections.abc import AsyncIterator  # noqa: E402

from app.providers.llm import CallContext, LLMProvider, LLMResult, StreamChunk  # noqa: E402

_SRC = re.compile(r'<source id="(S\d+)"[^>]*>\n(.*?)\n</source>', re.S)
_Q = re.compile(r"<question>\n(.*?)\n</question>", re.S)


class FakeLLM(LLMProvider):
    """Answers from the supplied sources only, citing the best-overlapping passage."""

    name = "fake"

    def __init__(self, model: str = "fake-model") -> None:
        self.model = model
        self.mode = "normal"  # normal | insufficient | fail | fail_midstream | invalid_marker
        self.calls: list[str] = []
        self.json_handlers: dict[str, Any] = {}

    def _answer(self, text: str) -> str:
        q = (_Q.search(text) or [None, ""])[1] if _Q.search(text) else ""
        qwords = set(_WORD.findall(q.lower()))
        best, best_score = None, 0
        for label, body in _SRC.findall(text):
            score = len(qwords & set(_WORD.findall(body.lower())))
            if score > best_score:
                best, best_score = (label, body), score
        if self.mode == "insufficient" or not best:
            return "INSUFFICIENT_EVIDENCE\nThe library does not cover this."
        label, body = best
        sentence = body.strip().split(". ")[0][:200]
        extra = " See also [S99]." if self.mode == "invalid_marker" else ""
        return f"According to the sources, {sentence} [{label}].{extra}"

    async def generate(self, *, system, messages, max_output_tokens, temperature=0.2, json_schema=None, ctx=None):  # type: ignore[no-untyped-def]
        ctx = ctx or CallContext()
        self.calls.append(ctx.operation)
        if self.mode == "fail":
            from app.core.errors import ProviderError

            raise ProviderError("provider down", code="provider_unavailable", retryable=True, status_code=503)
        text = messages[-1].text
        if json_schema is not None:
            handler = self.json_handlers.get(ctx.operation)
            data = handler(text) if handler else {}
            import json as _json

            return LLMResult(_json.dumps(data), len(text) // 4, 50, self.model, data=data)
        out = self._answer(text)
        return LLMResult(out, len(text) // 4, len(out) // 4, self.model)

    async def stream(self, *, system, messages, max_output_tokens, temperature=0.2, ctx=None) -> AsyncIterator[StreamChunk]:  # type: ignore[no-untyped-def,override]
        ctx = ctx or CallContext()
        self.calls.append(ctx.operation)
        from app.core.errors import ProviderError

        if self.mode == "fail":
            raise ProviderError("provider down", code="provider_unavailable", retryable=True, status_code=503)
        out = self._answer(messages[-1].text)
        words = out.split(" ")
        for i, w in enumerate(words):
            if self.mode == "fail_midstream" and i == 3:
                raise ProviderError("connection reset", code="provider_unavailable", retryable=True, status_code=503)
            yield StreamChunk(text=w + (" " if i < len(words) - 1 else ""))
        yield StreamChunk(done=True, result=LLMResult(out, len(messages[-1].text) // 4, len(out) // 4, self.model))


FAKE_LLM = FakeLLM()


def install_llm() -> None:
    from app.providers.llm import set_llm

    for purpose in ("answer", "research", "extraction"):
        set_llm(purpose, FAKE_LLM)  # type: ignore[arg-type]


# --- JSON handlers for structured operations ---------------------------------------------

_PASSAGE = re.compile(r'<passage id="([CE]\d+)"[^>]*>\n(.*?)\n</passage>', re.S)
_CAP = re.compile(r"\b([A-Z][a-z]{3,}(?:\s[A-Z][a-z]{3,})?)\b")
_STOP = {
    "Chapter",
    "Merchants",
    "Shepherds",
    "Climbers",
    "Beekeepers",
    "Honeybees",
    "According",
    "Tell",
    "What",
    "Which",
    "This",
    "That",
    "There",
}


def _graph(text: str) -> dict[str, Any]:
    ents: dict[str, dict[str, Any]] = {}
    rels = []
    for label, body in _PASSAGE.findall(text):
        names = [n for n in dict.fromkeys(_CAP.findall(body)) if n.split()[0] not in _STOP][:5]
        for n in names:
            ents.setdefault(
                n,
                {
                    "name": n,
                    "type": "location" if n in ("Tbilisi", "Kazbek", "Mtkvari", "Caucasus") else "concept",
                    "description": "",
                    "passages": [],
                },
            )["passages"].append(label)
        if len(names) >= 2:
            rels.append(
                {
                    "source": names[0],
                    "target": names[1],
                    "type": "located_in",
                    "description": f"{names[0]} relates to {names[1]}",
                    "passages": [label],
                }
            )
    rels.append({"source": "Ghost", "target": "Nowhere", "type": "related_to", "passages": ["C1"]})  # invalid endpoints: must be dropped
    return {"entities": list(ents.values()), "relationships": rels}


def _plan(text: str) -> dict[str, Any]:
    q = re.search(r"<research_question>\n?(.*?)\n?</research_question>", text, re.S)
    question = q.group(1).strip() if q else "topic"
    parts = [p.strip() for p in re.split(r"\band\b|,", question) if p.strip()]
    return {
        "title": f"Research: {question[:60]}",
        "outline": ["Background", "Findings"],
        "subquestions": [f"What does the library say about {p}?" for p in parts][:4],
    }


def _critique(text: str) -> dict[str, Any]:
    labels = [lb for lb, _ in _PASSAGE.findall(text)]
    return {
        "gaps": [{"subquestion": "Q1", "missing": "More detail on honey", "follow_up_query": "chestnut honey beekeepers"}],
        "conflicts": [{"description": "Sources differ on timing", "evidence": labels[:2] + ["E999"]}] if len(labels) >= 2 else [],
        "coverage": "medium",
    }


def _synthesis(text: str) -> dict[str, Any]:
    labels = [lb for lb, _ in _PASSAGE.findall(text)]
    first = labels[0] if labels else "E1"
    second = labels[1] if len(labels) > 1 else first
    return {
        "title": "Synthesized report",
        "executive_summary": f"The library describes several themes [{first}] and also an unsupported claim [E999].",
        "findings": [
            {"heading": "Main theme", "body": f"Evidence shows the theme clearly [{first}, {second}].", "kind": "supported"},
            {"heading": "Uncited claim", "body": "This is presented as supported but has no citation.", "kind": "supported"},
            {"heading": "Speculation", "body": f"Perhaps there is a link [{second}].", "kind": "hypothesis"},
        ],
        "contradictions": ["Timing differs between sources"],
        "cross_source_connections": [f"Both chapters mention the region [{first}]."],
        "limitations": ["Only one book in the library."],
        "open_questions": ["How old is the practice?"],
    }


FAKE_LLM.json_handlers.update(
    {"graph_extraction": _graph, "research_plan": _plan, "research_critique": _critique, "research_synthesis": _synthesis}
)
