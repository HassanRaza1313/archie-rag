"""Retrieval pipeline: hybrid search -> fusion -> rerank -> relevance floor -> dedup -> compression -> budget.

Every step is a small pure function so it can be tested and traced on its own. ``retrieve`` reports the
candidate count after each step (the funnel) and a confidence in [0, 1].
"""

from __future__ import annotations

import asyncio
import hashlib
import statistics
from dataclasses import dataclass
from typing import Any

from ragkit.config import KnowledgeBaseConfig
from ragkit.index import Hit, VectorIndex
from ragkit.providers import Reranker
from ragkit.textutil import (
    bm25_query_vector,
    estimate_tokens,
    jaccard,
    keyword_terms,
    shingles,
    split_sentences,
)


@dataclass
class Candidate:
    id: str
    text: str
    payload: dict[str, Any]
    dense_rank: int | None = None
    dense_score: float | None = None
    keyword_rank: int | None = None
    keyword_score: float | None = None
    fused_score: float = 0.0
    relevance: float = 0.0

    def summary(self, rank: int) -> dict[str, Any]:
        return {
            "rank": rank,
            "chunk_id": self.id,
            "file": self.payload.get("file_name"),
            "section": self.payload.get("section"),
            "page": self.payload.get("page_start"),
            "dense_rank": self.dense_rank,
            "keyword_rank": self.keyword_rank,
            "fused": round(self.fused_score, 4),
            "relevance": round(self.relevance, 3),
            "text": self.text,
        }


@dataclass
class RetrievalResult:
    selected: list[Candidate]
    ranked: list[Candidate]
    funnel: dict[str, int]
    confidence: float


def to_candidates(dense: list[Hit], keyword: list[Hit]) -> list[Candidate]:
    merged: dict[str, Candidate] = {}
    for rank, hit in enumerate(dense, start=1):
        merged[hit.id] = Candidate(
            hit.id, str(hit.payload.get("text", "")), hit.payload, dense_rank=rank, dense_score=hit.score
        )
    for rank, hit in enumerate(keyword, start=1):
        candidate = merged.setdefault(hit.id, Candidate(hit.id, str(hit.payload.get("text", "")), hit.payload))
        candidate.keyword_rank, candidate.keyword_score = rank, hit.score
    return list(merged.values())


def fuse_rrf(candidates: list[Candidate], k: int) -> list[Candidate]:
    for c in candidates:
        c.fused_score = sum(1.0 / (k + rank) for rank in (c.dense_rank, c.keyword_rank) if rank is not None)
    return sorted(candidates, key=lambda c: c.fused_score, reverse=True)


def fuse_dbsf(candidates: list[Candidate]) -> list[Candidate]:
    """Distribution-based score fusion: each retriever's scores scaled by mean +/- 3 std, then summed."""

    def scaler(scores: list[float]) -> tuple[float, float]:
        if len(scores) < 2:
            return (scores[0] - 1.0, 2.0) if scores else (0.0, 1.0)
        mean, std = statistics.fmean(scores), statistics.pstdev(scores)
        return (mean - 3 * std, 6 * std) if std else (mean - 1.0, 2.0)

    dense_low, dense_span = scaler([c.dense_score for c in candidates if c.dense_score is not None])
    kw_low, kw_span = scaler([c.keyword_score for c in candidates if c.keyword_score is not None])
    for c in candidates:
        c.fused_score = 0.0
        if c.dense_score is not None:
            c.fused_score += min(1.0, max(0.0, (c.dense_score - dense_low) / dense_span))
        if c.keyword_score is not None:
            c.fused_score += min(1.0, max(0.0, (c.keyword_score - kw_low) / kw_span))
    return sorted(candidates, key=lambda c: c.fused_score, reverse=True)


def heuristic_relevance(candidate: Candidate) -> float:
    """Relevance without a reranker.

    shortcut: fixed calibration (cosine 0.35->0, 0.75->1; BM25 s/(s+5)); calibrate per embedding model if the
    reranker is disabled in production.
    """
    if candidate.dense_score is not None:
        return min(1.0, max(0.0, (candidate.dense_score - 0.35) / 0.40))
    score = candidate.keyword_score or 0.0
    return score / (score + 5.0)


def confidence_of(ranked: list[Candidate]) -> float:
    """Top relevance, nudged up when two of the top three come from the same document section."""
    if not ranked:
        return 0.0
    top = ranked[0].relevance
    keys = [(c.payload.get("document_uri"), c.payload.get("section")) for c in ranked[:3]]
    agreement = len(keys) != len(set(keys))
    return round(min(1.0, top + (0.05 if agreement else 0.0)), 4)


def deduplicate(candidates: list[Candidate], threshold: float) -> list[Candidate]:
    kept: list[Candidate] = []
    seen_hashes: set[str] = set()
    kept_shingles: list[set[int]] = []
    for candidate in candidates:
        digest = hashlib.sha1(" ".join(candidate.text.split()).encode(), usedforsecurity=False).hexdigest()
        if digest in seen_hashes:
            continue
        grams = shingles(candidate.text)
        if any(jaccard(grams, other) >= threshold for other in kept_shingles):
            continue
        seen_hashes.add(digest)
        kept_shingles.append(grams)
        kept.append(candidate)
    return kept


def compress(candidate: Candidate, query: str) -> str:
    """Keep the first sentence and every sentence sharing a keyword with the query; tables are kept whole."""
    if candidate.payload.get("content_type") == "table":
        return candidate.text
    query_terms = set(keyword_terms(query))
    sentences = split_sentences(candidate.text)
    kept = [s for i, s in enumerate(sentences) if i == 0 or query_terms & set(keyword_terms(s))]
    return " ".join(kept) if kept else candidate.text


def fit_budget(candidates: list[Candidate], top_n: int, token_budget: int) -> list[Candidate]:
    selected, used = [], 0
    for candidate in candidates:
        cost = estimate_tokens(candidate.text)
        if used + cost > token_budget:
            continue
        selected.append(candidate)
        used += cost
        if len(selected) >= top_n:
            break
    return selected


async def retrieve(
    query: str,
    query_vector: list[float] | None,
    *,
    collection: str,
    config: KnowledgeBaseConfig,
    index: VectorIndex,
    reranker: Reranker | None,
    filters: dict[str, Any] | None,
    candidates: int,
) -> RetrievalResult:
    mode = config.retrieval.mode
    dense_vector = query_vector if mode in ("hybrid", "dense") else None
    sparse_vector = bm25_query_vector(query) if mode in ("hybrid", "keyword") else None
    dense, keyword = await asyncio.to_thread(
        index.search, collection, dense_vector, sparse_vector, candidates, filters, config.retrieval.search_ef
    )
    pool = to_candidates(dense, keyword)
    fused = fuse_dbsf(pool) if config.fusion.method == "dbsf" else fuse_rrf(pool, config.fusion.rrf_k)
    funnel = {"dense": len(dense), "keyword": len(keyword), "fused": len(fused)}

    if config.rerank.enabled and reranker is not None and fused:
        scores = await asyncio.to_thread(reranker.score, query, [c.text for c in fused])
        for candidate, score in zip(fused, scores, strict=True):
            candidate.relevance = score
        ranked = sorted(fused, key=lambda c: c.relevance, reverse=True)
        funnel["reranked"] = len(ranked)
    else:
        for candidate in fused:
            candidate.relevance = heuristic_relevance(candidate)
        ranked = fused

    relevant = [c for c in ranked if c.relevance >= config.rerank.min_relevance]
    funnel["relevant"] = len(relevant)
    unique = deduplicate(relevant, config.context.near_duplicate_threshold) if config.context.dedup else relevant
    funnel["deduplicated"] = len(unique)
    if config.context.compression == "extractive":
        for candidate in unique:
            candidate.text = compress(candidate, query)
    selected = fit_budget(unique, config.rerank.top_n, config.context.token_budget)
    funnel["context"] = len(selected)
    funnel["context_tokens"] = sum(estimate_tokens(c.text) for c in selected)
    return RetrievalResult(selected, ranked, funnel, confidence_of(relevant))
