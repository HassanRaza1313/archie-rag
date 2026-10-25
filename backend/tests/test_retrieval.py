from __future__ import annotations

import pytest

from ragkit.index import Hit
from ragkit.retrieval import (
    Candidate,
    compress,
    confidence_of,
    deduplicate,
    fit_budget,
    fuse_dbsf,
    fuse_rrf,
    heuristic_relevance,
    to_candidates,
)


def hit(identifier: str, score: float, **payload: object) -> Hit:
    return Hit(identifier, score, {"text": f"text {identifier}", **payload})


def test_to_candidates_merges_ranks_from_both_retrievers() -> None:
    merged = {c.id: c for c in to_candidates([hit("a", 0.9), hit("b", 0.8)], [hit("b", 7.0), hit("c", 3.0)])}
    assert (merged["a"].dense_rank, merged["a"].keyword_rank) == (1, None)
    assert (merged["b"].dense_rank, merged["b"].keyword_rank, merged["b"].keyword_score) == (2, 1, 7.0)
    assert (merged["c"].dense_rank, merged["c"].keyword_rank) == (None, 2)


def test_rrf_rewards_agreement() -> None:
    ranked = fuse_rrf(to_candidates([hit("a", 0.9), hit("b", 0.8)], [hit("b", 7.0), hit("c", 3.0)]), k=60)
    assert [c.id for c in ranked] == ["b", "a", "c"]
    assert ranked[0].fused_score == pytest.approx(1 / 62 + 1 / 61)


def test_dbsf_normalises_each_retriever() -> None:
    dense = [hit("a", 0.90), hit("b", 0.89), hit("c", 0.10)]
    keyword = [hit("c", 30.0), hit("a", 1.0)]
    ranked = fuse_dbsf(to_candidates(dense, keyword))
    assert ranked[0].id in {"a", "c"}
    assert all(0.0 <= c.fused_score <= 2.0 for c in ranked)


def test_dbsf_handles_single_and_identical_scores() -> None:
    ranked = fuse_dbsf(to_candidates([hit("a", 0.5)], []))
    assert ranked[0].fused_score == pytest.approx(0.5)
    ranked = fuse_dbsf(to_candidates([hit("a", 0.5), hit("b", 0.5)], []))
    assert ranked[0].fused_score == ranked[1].fused_score


def test_heuristic_relevance_is_bounded() -> None:
    assert heuristic_relevance(Candidate("a", "", {}, dense_score=0.95)) == 1.0
    assert heuristic_relevance(Candidate("a", "", {}, dense_score=0.1)) == 0.0
    assert heuristic_relevance(Candidate("a", "", {}, keyword_score=5.0)) == 0.5


def test_confidence_is_top_relevance_with_agreement_bonus() -> None:
    def candidate(relevance: float, doc: str, section: str) -> Candidate:
        return Candidate(doc + section, "", {"document_uri": doc, "section": section}, relevance=relevance)

    assert confidence_of([]) == 0.0
    assert confidence_of([candidate(0.6, "d1", "s1"), candidate(0.5, "d2", "s1")]) == 0.6
    assert confidence_of([candidate(0.6, "d1", "s1"), candidate(0.5, "d1", "s1")]) == 0.65
    assert confidence_of([candidate(0.99, "d1", "s1"), candidate(0.9, "d1", "s1")]) == 1.0


def test_deduplicate_exact_and_near_duplicates() -> None:
    base = "the pile should feel like a wrung out sponge damp but not dripping and turned weekly"
    candidates = [
        Candidate("1", base, {}),
        Candidate("2", "  " + base + " ", {}),
        Candidate("3", base + " always", {}),
        Candidate("4", "completely unrelated text about bikes", {}),
    ]
    assert [c.id for c in deduplicate(candidates, threshold=0.8)] == ["1", "4"]
    assert [c.id for c in deduplicate(candidates, threshold=1.0)] == ["1", "3", "4"]


def test_compress_keeps_first_and_matching_sentences() -> None:
    candidate = Candidate("1", "Intro sentence here. Bikes need oil. Compost needs air. More compost facts.", {})
    assert compress(candidate, "how does compost work") == "Intro sentence here. Compost needs air. More compost facts."
    table = Candidate("2", "| a | b |", {"content_type": "table"})
    assert compress(table, "anything") == "| a | b |"


def test_fit_budget_skips_oversized_and_respects_top_n() -> None:
    big = Candidate("big", "x" * 4000, {})
    small = [Candidate(str(i), "y" * 40, {}) for i in range(5)]
    selected = fit_budget([big, *small], top_n=3, token_budget=100)
    assert [c.id for c in selected] == ["0", "1", "2"]
    assert fit_budget([], 3, 100) == []
