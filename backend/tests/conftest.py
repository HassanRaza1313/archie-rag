"""Shared fixtures: deterministic fake model providers, isolated settings, and the sample corpus."""

from __future__ import annotations

import hashlib
import math
import shutil
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from ragkit.providers import ChatOptions, EmbedKind, ProviderError
from ragkit.settings import Settings
from ragkit.textutil import keyword_terms

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "sources" / "sample"
GOLDEN_FILE = Path(__file__).resolve().parents[2] / "data" / "eval" / "sample-golden.jsonl"
DIMENSION = 64


def fake_vector(text: str) -> list[float]:
    """Hashed bag-of-words: texts sharing keywords get high cosine similarity."""
    vector = [0.0] * DIMENSION
    for term in keyword_terms(text):
        digest = hashlib.md5(term.encode(), usedforsecurity=False).digest()
        vector[digest[0] % DIMENSION] += 1.0 if digest[1] % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


ChatScript = Callable[[list[dict[str, str]]], str]


def default_script(messages: list[dict[str, str]]) -> str:
    prompt = messages[-1]["content"]
    if prompt.startswith("Rewrite the follow-up"):
        return prompt.rsplit("Follow-up question:", 1)[1].strip() + " (standalone)"
    if prompt.startswith("A search for the question"):
        return prompt.rsplit("Question:", 1)[1].strip() + " rewritten"
    if prompt.startswith("Split the question"):
        return prompt.rsplit("Question:", 1)[1].strip()
    if prompt.startswith("Do the sources support"):
        return '{"verdict": "supported"}'
    return "According to the documents, the answer is described in the first source in detail [S1]."


class FakeProvider:
    def __init__(self, models: list[str] | None = None, remote: bool = False) -> None:
        self.models = models if models is not None else ["nomic-embed-text:latest", "qwen3.5:9b"]
        self.is_remote = remote
        self.script: ChatScript = default_script
        self.chat_calls: list[list[dict[str, str]]] = []
        self.embed_calls = 0
        self.fail_chat: Exception | None = None
        self.fail_embed: Exception | None = None

    async def embed(self, model: str, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        if self.fail_embed:
            raise self.fail_embed
        self.embed_calls += 1
        return [fake_vector(text) for text in texts]

    async def chat(self, messages: list[dict[str, str]], options: ChatOptions) -> AsyncIterator[str]:
        self.chat_calls.append(messages)
        if self.fail_chat:
            raise self.fail_chat
        reply = self.script(messages)
        for word in reply.split(" "):
            yield word + " "
        options.usage.prompt_tokens, options.usage.completion_tokens = 100, len(reply.split())

    async def list_models(self) -> list[str]:
        return self.models

    async def ping(self) -> bool:
        return True


class FakeReranker:
    """Relevance = share of query keywords present in the text."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        terms = set(keyword_terms(query))
        return [len(terms & set(keyword_terms(text))) / len(terms) if terms else 0.0 for text in texts]


class FakeRegistry:
    def __init__(self) -> None:
        self.provider = FakeProvider()
        self.rerank_enabled = True

    def get(self, name: str) -> FakeProvider:
        if name not in ("ollama", "openai"):
            raise ProviderError(f"unknown provider '{name}'")
        return self.provider

    def reranker(self, model: str) -> FakeReranker | None:
        return FakeReranker() if self.rerank_enabled else None

    async def close(self) -> None:
        return None


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    target = tmp_path / "data" / "sample"
    shutil.copytree(SAMPLE_DIR, target)
    return target


@pytest.fixture
def settings(tmp_path: Path, corpus: Path) -> Settings:
    return Settings(
        state_dir=tmp_path / "state",
        data_roots=[tmp_path / "data"],
        qdrant_mode="embedded",
        rerank_provider="fastembed",
        cors_origins=["http://testserver"],
    )


@pytest.fixture
def registry() -> FakeRegistry:
    return FakeRegistry()


def kb_overrides(corpus: Path, **extra: Any) -> dict[str, Any]:
    from ragkit.config import deep_merge

    return deep_merge({"source": {"path": str(corpus)}}, extra)
