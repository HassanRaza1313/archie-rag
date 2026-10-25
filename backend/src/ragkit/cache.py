"""Answer cache: exact match first, then a guarded paraphrase match.

Rows live in SQLite per scope ``(kb, index hash, query-config hash)``, so a rebuild or a config change starts a
fresh cache. Each answered question bumps its ask count; the ``capacity`` most-asked questions of the last
``history`` questions form the active cache (frequency first, recency as tie-break).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from ragkit.config import CacheConfig
from ragkit.storage import Database
from ragkit.textutil import normalize_question

_NEGATION = re.compile(r"\b(?:not|no|never|none|nobody|nothing|without|cannot)\b|n't\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class CacheHit:
    answer: str
    citations: list[dict[str, Any]]
    question: str
    match: str  # "exact" | "semantic"
    similarity: float


def scope_key(kb_id: str, index_hash: str, query_hash: str) -> str:
    return f"{kb_id}:{index_hash[:10]}:{query_hash[:10]}"


def same_meaning_markers(left: str, right: str) -> bool:
    """Reject paraphrase hits that differ in negation, numbers or named entities ('is X' vs 'isn't X')."""

    def markers(text: str) -> tuple[bool, set[str], set[str]]:
        tokens = [token.strip(".,;:!?\"'()") for token in text.split()]
        entities = {token.lower() for token in tokens[1:] if token[:1].isupper()}  # skip the sentence-initial word
        return bool(_NEGATION.search(text)), set(_NUMBER.findall(text)), entities

    return markers(left) == markers(right)


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


class AnswerCache:
    def __init__(self, db: Database) -> None:
        self.db = db

    def lookup(self, scope: str, question: str, embedding: list[float] | None, config: CacheConfig) -> CacheHit | None:
        rows = self.db.cache_rows(scope, config.capacity)
        key = normalize_question(question)
        for row in rows:
            if row["question_key"] == key:
                return self._hit(scope, row, "exact", 1.0)
        if not (config.semantic and embedding):
            return None
        best, best_score = None, 0.0
        for row in rows:
            if row["embedding"]:
                score = _cosine(embedding, json.loads(row["embedding"]))
                if score > best_score:
                    best, best_score = row, score
        if best and best_score >= config.semantic_threshold and same_meaning_markers(question, best["question"]):
            return self._hit(scope, best, "semantic", best_score)
        return None

    def _hit(self, scope: str, row: dict[str, Any], match: str, similarity: float) -> CacheHit:
        self.db.cache_touch(scope, row["question_key"])
        return CacheHit(row["answer"], json.loads(row["citations"]), row["question"], match, round(similarity, 4))

    def record(
        self,
        scope: str,
        question: str,
        embedding: list[float] | None,
        answer: str,
        citations: list[dict[str, Any]],
        config: CacheConfig,
    ) -> None:
        self.db.cache_record(
            scope,
            normalize_question(question),
            question,
            embedding,
            answer,
            citations,
            max(config.history, config.capacity),
        )
