from __future__ import annotations

import json

import httpx
import pytest

from ragkit.providers import (
    ChatOptions,
    OllamaProvider,
    OpenAIProvider,
    ProviderError,
    ProviderRegistry,
    _sigmoid,
    embed_prefix,
)
from ragkit.settings import Settings


def client_for(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


def test_embed_prefixes() -> None:
    assert embed_prefix("nomic-embed-text", "document") == "search_document: "
    assert embed_prefix("nomic-embed-text:latest", "query") == "search_query: "
    assert embed_prefix("multilingual-e5-large", "query") == "query: "
    assert embed_prefix("bge-m3", "query") == ""


async def test_ollama_embed_batches_with_prefix() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[0.1], [0.2]]})

    provider = OllamaProvider(client_for(httpx.MockTransport(handler)), "http://localhost:11434/")
    assert await provider.embed("nomic-embed-text", ["a", "b"], "document") == [[0.1], [0.2]]
    assert seen == [{"model": "nomic-embed-text", "input": ["search_document: a", "search_document: b"]}]
    assert provider.is_remote is False


async def test_ollama_chat_streams_tokens_and_usage() -> None:
    lines = [
        {"message": {"content": "Hel"}},
        {"message": {"content": "lo"}},
        {"done": True, "prompt_eval_count": 12, "eval_count": 2},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["think"] is False and body["options"]["num_ctx"] == 4096
        return httpx.Response(200, content="\n".join(json.dumps(line) for line in lines).encode())

    provider = OllamaProvider(client_for(httpx.MockTransport(handler)), "http://localhost:11434")
    options = ChatOptions(model="m", context_window=4096)
    assert "".join([t async for t in provider.chat([{"role": "user", "content": "hi"}], options)]) == "Hello"
    assert (options.usage.prompt_tokens, options.usage.completion_tokens) == (12, 2)


async def test_ollama_chat_surfaces_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'x' not found"})

    provider = OllamaProvider(client_for(httpx.MockTransport(handler)), "http://localhost:11434")
    with pytest.raises(ProviderError, match="not found"):
        _ = [t async for t in provider.chat([], ChatOptions(model="x"))]


async def test_ollama_in_stream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"error": "out of memory"}\n')

    provider = OllamaProvider(client_for(httpx.MockTransport(handler)), "http://localhost:11434")
    with pytest.raises(ProviderError, match="out of memory"):
        _ = [t async for t in provider.chat([], ChatOptions(model="x"))]


async def test_retries_server_errors_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"embeddings": [[1.0]]})

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("ragkit.providers.asyncio.sleep", no_sleep)
    provider = OllamaProvider(client_for(httpx.MockTransport(handler)), "http://localhost:11434")
    assert await provider.embed("m", ["x"], "query") == [[1.0]]
    assert calls["n"] == 3


async def test_unreachable_provider_raises_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("ragkit.providers.asyncio.sleep", no_sleep)
    provider = OllamaProvider(client_for(httpx.MockTransport(handler)), "http://localhost:11434")
    with pytest.raises(ProviderError, match="cannot reach"):
        await provider.embed("m", ["x"], "query")
    assert await provider.ping() is False


async def test_openai_embed_chat_and_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-test"
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200, json={"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}
            )
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]})
        chunks = [
            {"choices": [{"delta": {"content": "Hi"}}]},
            {"choices": [{"delta": {}}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 1}},
        ]
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=body.encode())

    provider = OpenAIProvider(client_for(httpx.MockTransport(handler)), "https://api.example.com/v1", "sk-test")
    assert provider.is_remote is True
    assert await provider.embed("text-embedding-3-small", ["a", "b"], "document") == [[1.0], [2.0]]
    options = ChatOptions(model="gpt")
    assert "".join([t async for t in provider.chat([], options)]) == "Hi"
    assert options.usage.prompt_tokens == 5
    assert await provider.list_models() == ["a", "b"]


async def test_registry_resolves_and_disables_reranker() -> None:
    registry = ProviderRegistry(Settings(rerank_provider="none"))
    try:
        assert isinstance(registry.get("ollama"), OllamaProvider)
        assert registry.reranker("any") is None
        with pytest.raises(ProviderError):
            registry.get("nope")
    finally:
        await registry.close()


def test_sigmoid_is_stable() -> None:
    assert _sigmoid(0) == 0.5
    assert _sigmoid(1000) == pytest.approx(1.0) and _sigmoid(-1000) == pytest.approx(0.0)
