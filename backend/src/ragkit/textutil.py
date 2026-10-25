"""Text helpers shared by chunking, keyword search, dedup, compression and the answer cache."""

from __future__ import annotations

import math
import re
import zlib
from collections import Counter

_WORD = re.compile(r"\w+", re.UNICODE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "he",
        "her",
        "his",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "she",
        "so",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    }
)


def estimate_tokens(text: str) -> int:
    return estimate_tokens_for_length(len(text))


def estimate_tokens_for_length(characters: int) -> int:
    # shortcut: ~4 characters per token for English; plug in the model tokenizer if budgets must be exact.
    return max(1, math.ceil(characters / 4))


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def keyword_terms(text: str) -> list[str]:
    return [w for w in words(text) if w not in STOPWORDS and (len(w) > 1 or w.isdigit())]


def term_id(term: str) -> int:
    return zlib.crc32(term.encode("utf-8"))


def bm25_document_vector(text: str, k1: float = 1.2, b: float = 0.75, avg_len: float = 256.0) -> dict[int, float]:
    """BM25 term-frequency weights; Qdrant multiplies in IDF (sparse vector ``modifier=idf``).

    shortcut: fixed average document length (as FastEmbed's BM25 does); per-collection averages would be exact.
    """
    terms = keyword_terms(text)
    counts = Counter(terms)
    norm = k1 * (1 - b + b * len(terms) / avg_len)
    return {term_id(t): tf * (k1 + 1) / (tf + norm) for t, tf in counts.items()}


def bm25_query_vector(text: str) -> dict[int, float]:
    return {term_id(t): 1.0 for t in set(keyword_terms(text))}


def shingles(text: str, size: int = 5) -> set[int]:
    tokens = words(text)
    if len(tokens) < size:
        return {hash(" ".join(tokens))}
    return {hash(" ".join(tokens[i : i + size])) for i in range(len(tokens) - size + 1)}


def jaccard(left: set[int], right: set[int]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def normalize_question(text: str) -> str:
    return " ".join(words(text))
