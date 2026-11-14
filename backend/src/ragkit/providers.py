"""Model providers: embeddings and chat (Ollama, OpenAI-compatible, Claude), local ONNX embeddings (FastEmbed),
and cross-encoder reranking. Besides the built-in ``ollama``, ``openai`` and ``fastembed``, every connection added
in the app (``conn_…``) is a provider.

Add a provider kind by implementing ``ModelProvider`` and building it in ``ProviderRegistry.build``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlparse

import httpx

from ragkit import connections
from ragkit.settings import Settings
from ragkit.telemetry import request_id_var

if TYPE_CHECKING:
    from ragkit.storage import Database

EmbedKind = Literal["document", "query"]

# Instruction prefixes some embedding models were trained with; missing them costs retrieval quality.
_EMBED_PREFIXES: dict[str, tuple[str, str]] = {
    "nomic-embed": ("search_document: ", "search_query: "),
    "e5": ("passage: ", "query: "),
    "mxbai-embed": ("", "Represent this sentence for searching relevant passages: "),
    "bge-small-en": ("", "Represent this sentence for searching relevant passages: "),
    "bge-base-en": ("", "Represent this sentence for searching relevant passages: "),
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


# Models that take the server-side fallback on a policy decline (the API re-runs the request on a fallback model).
_FALLBACK_MODELS = ("claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")


def _supports_effort(model: str) -> bool:
    """Effort exists from the 4.6 models on; older ones reject it."""
    match = re.match(r"claude-[a-z]+-(\d+)(?:-(\d+))?", model)
    if not match:
        return True  # an alias or a new naming scheme: assume a current model
    major, minor = int(match.group(1)), int(match.group(2) or 0)
    return major >= 5 or (major == 4 and minor >= 6)


@contextlib.contextmanager
def _claude_errors(model: str) -> Iterator[None]:
    import anthropic

    try:
        yield
    except anthropic.AuthenticationError as exc:
        raise ProviderError("Claude rejected the API key. Add the connection again with a valid key.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise ProviderError("This Claude key may not use this model or feature.") from exc
    except anthropic.NotFoundError as exc:
        raise ProviderError(f"Claude has no model '{model}'.") from exc
    except anthropic.RateLimitError as exc:
        raise ProviderError("Claude is limiting requests for this key. Try again shortly.") from exc
    except anthropic.APIStatusError as exc:
        raise ProviderError(f"Claude returned {exc.status_code}: {str(exc.message)[:300]}") from exc
    except anthropic.APIConnectionError as exc:
        raise ProviderError(f"cannot reach Claude: {exc}") from exc


class AnthropicProvider:
    """Claude through the official SDK. Chat only: Claude has no embedding models."""

    is_remote = True

    def __init__(self, api_key: str, base_url: str, timeout_s: float, http_client: Any = None) -> None:
        import anthropic

        self.client = anthropic.AsyncAnthropic(
            api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=2, http_client=http_client
        )

    async def embed(self, model: str, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        raise ProviderError("Claude has no search (embedding) models; use Archie's built-in one or another connection")

    async def chat(self, messages: list[dict[str, str]], options: ChatOptions) -> AsyncIterator[str]:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        request: dict[str, Any] = {
            "model": options.model,
            # Room for adaptive thinking on top of the answer; the prompt keeps answers short.
            "max_tokens": options.max_tokens + 16000,
            "messages": [m for m in messages if m["role"] != "system"],
        }
        if system:
            request["system"] = system
        if _supports_effort(options.model):
            request["output_config"] = {"effort": "medium" if options.thinking else "low"}
        if options.model in _FALLBACK_MODELS:
            request |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        with _claude_errors(options.model):
            async with self.client.beta.messages.stream(**request) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
        options.usage.prompt_tokens = final.usage.input_tokens
        options.usage.completion_tokens = final.usage.output_tokens
        if final.stop_reason == "refusal":
            raise ProviderError("Claude declined to answer this question.")

    async def list_models(self) -> list[str]:
        with _claude_errors(""):
            return sorted([model.id async for model in self.client.models.list()])

    async def ping(self) -> bool:
        try:
            await self.list_models()
        except ProviderError:
            return False
        return True

    async def close(self) -> None:
        await self.client.close()


class FastEmbedProvider:
    """Local ONNX embeddings via FastEmbed: no model server needed. Embedding only; it has no chat models."""

    is_remote = False

    def __init__(self, cache_dir: Path | None) -> None:
        self.cache_dir = cache_dir
        self._models: dict[str, Any] = {}

    def _model(self, name: str) -> Any:
        if name not in self._models:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise ProviderError("local embeddings need the 'rerank' extra: pip install 'ragkit[rerank]'") from exc
            try:
                self._models[name] = TextEmbedding(name, cache_dir=str(self.cache_dir) if self.cache_dir else None)
            except ValueError as exc:
                raise ProviderError(f"'{name}' is not a FastEmbed embedding model") from exc
        return self._models[name]

    async def embed(self, model: str, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        prefix = embed_prefix(model, kind)
        encoder = await asyncio.to_thread(self._model, model)
        vectors = await asyncio.to_thread(lambda: list(encoder.embed([prefix + t for t in texts])))
        return [vector.tolist() for vector in vectors]

    async def chat(self, messages: list[dict[str, str]], options: ChatOptions) -> AsyncIterator[str]:
        raise ProviderError("fastembed provides embeddings only; choose Ollama or OpenAI for generation")
        yield ""  # pragma: no cover - makes this an async generator like the other providers

    async def list_models(self) -> list[str]:
        try:
            from fastembed import TextEmbedding
        except ImportError:
            return []
        return sorted(model["model"] for model in TextEmbedding.list_supported_models())

    async def ping(self) -> bool:
        return bool(await self.list_models())


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
    """Providers from settings and from the connections stored in the database; caches loaded reranker models."""

    def __init__(self, settings: Settings, db: Database | None = None) -> None:
        self.settings = settings
        self.db = db
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout_s, connect=10.0),
            limits=httpx.Limits(max_connections=32),
            event_hooks={"request": [_forward_request_id]},
        )
        self._providers: dict[str, ModelProvider] = {
            "ollama": OllamaProvider(self.client, settings.ollama_url),
            "openai": OpenAIProvider(self.client, settings.openai_base_url, settings.openai_api_key),
            "fastembed": FastEmbedProvider(settings.model_cache_dir),
        }
        self._rerankers: dict[str, Reranker] = {}

    def get(self, name: str) -> ModelProvider:
        if name not in self._providers:
            row = self.db.get_connection(name) if self.db else None
            if row is None:
                raise ProviderError(f"the model connection '{name}' no longer exists; choose another in Setup")
            key = connections.decrypt(self.settings, row["api_key"]) if row["api_key"] else None
            if row["api_key"] and key is None:
                raise ProviderError(f"the key for {row['name']} can't be read here (a restored backup?); add it again")
            self._providers[name] = self.build(row["kind"], row["base_url"], key)
        return self._providers[name]

    def build(self, kind: str, base_url: str, api_key: str | None) -> ModelProvider:
        """A provider for a connection, also used to test one before it is saved."""
        if kind == "anthropic":
            return AnthropicProvider(api_key or "", base_url, self.settings.request_timeout_s)
        if kind == "ollama":
            return OllamaProvider(self.client, base_url)
        return OpenAIProvider(self.client, base_url, api_key)

    def forget(self, name: str) -> None:
        self._providers.pop(name, None)

    def reranker(self, model: str) -> Reranker | None:
        if self.settings.rerank_provider == "none":
            return None
        if model not in self._rerankers:
            self._rerankers[model] = FastEmbedReranker(model, self.settings.model_cache_dir)
        return self._rerankers[model]

    async def close(self) -> None:
        for provider in self._providers.values():
            if isinstance(provider, AnthropicProvider):
                await provider.close()
        await self.client.aclose()
