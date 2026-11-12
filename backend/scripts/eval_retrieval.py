"""Retrieval evaluation: Recall@K, MRR and citation accuracy.

Usage (against a configured environment and an existing workspace):

    python scripts/eval_retrieval.py --workspace <workspace_id> --dataset scripts/eval_dataset.example.json [--k 8] [--answers]

Dataset format (JSON list)::

    [{"question": "...", "expected": [{"source_title": "Book A", "contains": "a distinctive phrase"}]}]

A retrieved chunk counts as relevant when it belongs to the expected source and contains
the expected phrase (case-insensitive). With ``--answers`` the script also generates answers
(uses AI credits on the configured provider) and measures citation accuracy: the share of
answer citations that point at a relevant passage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bson import ObjectId  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.client import mongo  # noqa: E402
from app.retrieval.search import Hit, hybrid_search  # noqa: E402


def relevant(h: Hit, expected: list[dict[str, str]]) -> bool:
    for e in expected:
        if e.get("source_title") and e["source_title"].lower() not in (h.source_title or "").lower():
            continue
        if e.get("contains", "").lower() in h.text.lower():
            return True
    return False


def metrics(results: list[tuple[list[Hit], list[dict[str, str]]]], k: int) -> dict[str, float]:
    recall_hits = 0
    rr_sum = 0.0
    for hits, expected in results:
        top = hits[:k]
        flags = [relevant(h, expected) for h in top]
        if any(flags):
            recall_hits += 1
            rr_sum += 1.0 / (flags.index(True) + 1)
    n = max(1, len(results))
    return {f"recall@{k}": round(recall_hits / n, 4), "mrr": round(rr_sum / n, 4), "questions": len(results)}


async def run(workspace: str, dataset: Path, k: int, answers: bool) -> dict[str, Any]:
    await mongo.connect(get_settings())
    data = json.loads(dataset.read_text(encoding="utf-8"))
    ws = ObjectId(workspace)
    results = []
    for item in data:
        res = await hybrid_search(ws, item["question"], top_k=k)
        results.append((res.hits, item["expected"]))
    report: dict[str, Any] = metrics(results, k)
    if answers:
        from app.providers.llm import ChatMessage, get_llm
        from app.retrieval.rag import SYSTEM_PROMPT, build_sources_block, build_user_message, validate_citations
        from app.retrieval.search import select_context

        good = total = 0
        for (hits, expected), item in zip(results, data, strict=True):
            chosen = select_context(hits, 8000)
            block, mapping = build_sources_block(chosen)
            out = await get_llm("answer").generate(
                system=SYSTEM_PROMPT, messages=[ChatMessage("user", build_user_message(item["question"], block))], max_output_tokens=800
            )
            v = validate_citations(out.text, mapping)
            for c in v.citations:
                total += 1
                good += relevant(mapping[c["marker"]], expected)
        report["citation_accuracy"] = round(good / total, 4) if total else None
        report["citations_evaluated"] = total
    await mongo.close()
    return report


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--answers", action="store_true")
    a = p.parse_args()
    print(json.dumps(asyncio.run(run(a.workspace, a.dataset, a.k, a.answers)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
