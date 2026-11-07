"""Retrieval evaluation on a golden set, per config version.

A golden item names the files that should be retrieved for a question (``expected_sources``); an empty list
marks a question the corpus cannot answer, which the system should abstain on. Items may also name phrases that
must all appear in one retrieved chunk (``expected_text``), which measures parsing and chunking rather than
just finding the right file. Metrics: hit@1/3/5 and MRR over answerable items (per file, and per passage when
phrases are given), false-abstain rate, correct-abstain rate, and retrieval latency. No LLM calls are needed, so
two versions can be compared in seconds.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from typing import Any

from pydantic import BaseModel, Field

from ragkit.index import VectorIndex
from ragkit.providers import ProviderRegistry
from ragkit.retrieval import retrieve
from ragkit.storage import Database


class GoldenItem(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    expected_sources: list[str] = Field(default_factory=list)
    expected_text: list[str] = Field(default_factory=list)


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _contains_all(text: str, phrases: list[str]) -> bool:
    haystack = _normalise(text)
    return all(_normalise(phrase) in haystack for phrase in phrases)


async def evaluate(
    db: Database,
    index: VectorIndex,
    providers: ProviderRegistry,
    kb_id: str,
    items: list[GoldenItem],
    version: int | None = None,
) -> dict[str, Any]:
    kb = db.get_kb(kb_id)
    if kb is None or not kb["active_collection"]:
        raise ValueError("the knowledge base has not been built")
    version, config = db.get_config(kb_id, version)
    warnings = []
    if config.index_fingerprint() != kb["built_index_hash"]:
        warnings.append("this version has index settings that differ from the built index; results use the built index")
    embedder = providers.get(config.embedding.provider)
    reranker = None
    if config.rerank.enabled:
        try:
            reranker = await asyncio.to_thread(providers.reranker, config.rerank.model)
        except Exception as exc:
            warnings.append(f"reranker unavailable ({type(exc).__name__}); scored without it")

    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for item in items:
        started = time.perf_counter()
        vector = None
        if config.retrieval.mode != "keyword":
            vector = (await embedder.embed(config.embedding.model, [item.question], "query"))[0]
        result = await retrieve(
            item.question,
            vector,
            collection=kb["active_collection"],
            config=config,
            index=index,
            reranker=reranker,
            filters=None,
            candidates=config.retrieval.candidates,
        )
        latencies.append((time.perf_counter() - started) * 1000)
        abstained = not result.selected or result.confidence < config.agent.confidence_threshold
        expected = {source.lower() for source in item.expected_sources}
        rank = next(
            (
                position
                for position, candidate in enumerate(result.ranked, start=1)
                if str(candidate.payload.get("file_name", "")).lower() in expected
                or str(candidate.payload.get("document_uri", "")).lower() in expected
            ),
            None,
        )
        passage_rank = None
        if item.expected_text:
            passage_rank = next(
                (
                    position
                    for position, candidate in enumerate(result.ranked, start=1)
                    if _contains_all(candidate.text, item.expected_text)
                ),
                None,
            )
        context = " ".join(candidate.text for candidate in result.selected)
        rows.append(
            {
                "question": item.question,
                "answerable": bool(expected),
                "rank": rank,
                "passage_rank": passage_rank,
                "in_context": bool(item.expected_text) and _contains_all(context, item.expected_text),
                "abstained": abstained,
                "confidence": result.confidence,
            }
        )

    answerable = [row for row in rows if row["answerable"]]
    unanswerable = [row for row in rows if not row["answerable"]]

    with_text = [row for row, item in zip(rows, items, strict=True) if row["answerable"] and item.expected_text]

    def hit_at(k: int, key: str = "rank", pool: list[dict[str, Any]] = answerable) -> float | None:
        return _ratio(sum(1 for row in pool if row[key] and row[key] <= k), len(pool))

    passage = {
        "passage_hit_at_1": hit_at(1, "passage_rank", with_text),
        "passage_hit_at_3": hit_at(3, "passage_rank", with_text),
        "passage_hit_at_5": hit_at(5, "passage_rank", with_text),
        "context_recall": _ratio(sum(row["in_context"] for row in with_text), len(with_text)),
        "passage_mrr": _ratio(sum(1 / row["passage_rank"] for row in with_text if row["passage_rank"]), len(with_text)),
    }

    return {
        "kb_id": kb_id,
        "version": version,
        "items": len(rows),
        "metrics": {
            "hit_at_1": hit_at(1),
            "hit_at_3": hit_at(3),
            "hit_at_5": hit_at(5),
            "mrr": _ratio(sum(1 / row["rank"] for row in answerable if row["rank"]), len(answerable)),
            **(passage if with_text else {}),
            "false_abstain_rate": _ratio(sum(row["abstained"] for row in answerable), len(answerable)),
            "correct_abstain_rate": _ratio(sum(row["abstained"] for row in unanswerable), len(unanswerable)),
            "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else None,
            "latency_p95_ms": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 1) if latencies else None,
        },
        "warnings": warnings,
        "results": rows,
    }


def _ratio(numerator: float, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None
