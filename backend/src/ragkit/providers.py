"""Model providers: embeddings and chat (Ollama, OpenAI-compatible), and cross-encoder reranking.

Add a provider by implementing ``ModelProvider`` and registering it in ``PROVIDERS``.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx

from ragkit.settings import Settings
from ragkit.telemetry import request_id_var

EmbedKind = Literal["document", "query"]

# Instruction prefixes some embedding models were trained with; missing them costs retrieval quality.
_EMBED_PREFIXES: dict[str, tuple[str, str]] = {
    "nomic-embed": ("search_document: ", "search_query: "),
    "e5": ("passage: ", "query: "),
    "mxbai-embed": ("", "Represent this sentence for searching relevant passages: "),
}


def embed_prefix(model: str, kind: EmbedKind) -> str:
    for marker, (document_prefix, query_prefix) in _EMBED_PREFIXES.items():
        if marker in model.lower():
            return document_prefix if kind == "document" else query_prefix
    return ""


class ProviderError(RuntimeError):
    """A model provider call failed; the message is safe to show to users."""


@dataclass
class ChatUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class ChatOptions:
    model: str
    temperature: float = 0.1
    max_tokens: int = 800
    context_window: int = 8192
    thinking: bool = False
    usage: ChatUsage = field(default_factory=ChatUsage)


class ModelProvider(Protocol):
    is_remote: bool

    async def embed(self, model: str, texts: list[str], kind: EmbedKind) -> list[list[float]]: ...

    def chat(self, messages: list[dict[str, str]], options: ChatOptions) -> AsyncIterator[str]: ...

    async def list_models(self) -> list[str]: ...

    async def ping(self) -> bool: ...


class _HttpProvider:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.is_remote = urlparse(self.base_url).hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
            "host.docker.internal",
            "ollama",
        }

    async def _post_json(self, path: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        for attempt in range(3):
            try:
                response = await self.client.post(self.base_url + path, json=payload, headers=headers)
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise ProviderError(f"cannot reach {self.base_url}: {exc}") from exc
            else:
                if response.status_code < 500 or attempt == 2:
                    _raise_for_status(response)
                    return response.json()
            await asyncio.sleep(0.5 * 2**attempt)
        raise AssertionError("unreachable")

    async def ping(self) -> bool:
        try:
            await self.list_models()  # type: ignore[attr-defined]
        except Exception:
            return False
        return True


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("error", response.text)
    except ValueError:
        detail = response.text
    if isinstance(detail, dict):
        detail = detail.get("message", str(detail))
    raise ProviderError(f"{response.request.url.path} returned {response.status_code}: {str(detail)[:300]}")


class OllamaProvider(_HttpProvider):
    async def embed(self, model: str, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        prefix = embed_prefix(model, kind)
        body = await self._post_json("/api/embed", {"model": model, "input": [prefix + t for t in texts]})
        return list(body["embeddings"])

    async def chat(self, messages: list[dict[str, str]], options: ChatOptions) -> AsyncIterator[str]:
        payload = {
            "model": options.model,
            "messages": messages,
            "stream": True,
            "think": options.thinking,
            "options": {
                "temperature": options.temperature,
                "num_predict": options.max_tokens,
                "num_ctx": options.context_window,
            },
        }
        try:
            async with self.client.stream("POST", self.base_url + "/api/chat", json=payload) as response:
                if not response.is_success:
                    await response.aread()
                    _raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise ProviderError(str(chunk["error"]))
                    if text := chunk.get("message", {}).get("content"):
                        yield text
                    if chunk.get("done"):
                        options.usage.prompt_tokens = int(chunk.get("prompt_eval_count") or 0)
                        options.usage.completion_tokens = int(chunk.get("eval_count") or 0)
        except httpx.TransportError as exc:
            raise ProviderError(f"cannot reach {self.base_url}: {exc}") from exc

    async def list_models(self) -> list[str]:
        response = await self.client.get(self.base_url + "/api/tags")
        _raise_for_status(response)
        return sorted(model["name"] for model in response.json().get("models", []))


class OpenAIProvider(_HttpProvider):
    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str | None) -> None:
        super().__init__(client, base_url)
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def embed(self, model: str, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        prefix = embed_prefix(model, kind)
        body = await self._post_json(
            "/embeddings", {"model": model, "input": [prefix + t for t in texts]}, self.headers
        )
        return [item["embedding"] for item in sorted(body["data"], key=lambda item: item["index"])]

    async def chat(self, messages: list[dict[str, str]], options: ChatOptions) -> AsyncIterator[str]:
        payload = {
            "model": options.model,
            "messages": messages,
            "stream": True,
            "temperature": options.temperature,
            "max_tokens": options.max_tokens,
            "stream_options": {"include_usage": True},
        }
        try:
            async with self.client.stream(
                "POST", self.base_url + "/chat/completions", json=payload, headers=self.headers
            ) as response:
                if not response.is_success:
                    await response.aread()
                    _raise_for_status(response)
                async for line in response.aiter_lines():
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    chunk = json.loads(data)
                    if usage := chunk.get("usage"):
                        options.usage.prompt_tokens = int(usage.get("prompt_tokens") or 0)
                        options.usage.completion_tokens = int(usage.get("completion_tokens") or 0)
                    for choice in chunk.get("choices") or []:
                        if text := (choice.get("delta") or {}).get("content"):
                            yield text
        except httpx.TransportError as exc:
            raise ProviderError(f"cannot reach {self.base_url}: {exc}") from exc

    async def list_models(self) -> list[str]:
        response = await self.client.get(self.base_url + "/models", headers=self.headers)
        _raise_for_status(response)
        return sorted(model["id"] for model in response.json().get("data", []))


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]:
        """Relevance probabilities in [0, 1], one per text."""
        ...


class FastEmbedReranker:
    """ONNX cross-encoder via FastEmbed: CPU-friendly, no PyTorch."""

    def __init__(self, model: str, cache_dir: Path | None) -> None:
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError as exc:
            raise ProviderError("reranking needs the 'rerank' extra: pip install 'ragkit[rerank]'") from exc
        self._encoder = TextCrossEncoder(model_name=model, cache_dir=str(cache_dir) if cache_dir else None)

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [_sigmoid(logit) for logit in self._encoder.rerank(query, texts)]


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, float(value)))))


async def _forward_request_id(request: httpx.Request) -> None:
    if request_id := request_id_var.get():
        request.headers["X-Request-ID"] = request_id


class ProviderRegistry:
    """Creates providers from settings and caches loaded reranker models."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout_s, connect=10.0),
            limits=httpx.Limits(max_connections=32),
            event_hooks={"request": [_forward_request_id]},
        )
        self._providers: dict[str, ModelProvider] = {
            "ollama": OllamaProvider(self.client, settings.ollama_url),
            "openai": OpenAIProvider(self.client, settings.openai_base_url, settings.openai_api_key),
        }
        self._rerankers: dict[str, Reranker] = {}

    def get(self, name: str) -> ModelProvider:
        if name not in self._providers:
            raise ProviderError(f"unknown provider '{name}'")
        return self._providers[name]

    def reranker(self, model: str) -> Reranker | None:
        if self.settings.rerank_provider == "none":
            return None
        if model not in self._rerankers:
            self._rerankers[model] = FastEmbedReranker(model, self.settings.model_cache_dir)
        return self._rerankers[model]

    async def close(self) -> None:
        await self.client.aclose()
