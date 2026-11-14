"""Model connections: Claude through the official SDK, keys encrypted at rest, live tests, owner-only changes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from ragkit import connections
from ragkit.api import create_app
from ragkit.providers import AnthropicProvider, ChatOptions, ProviderError, ProviderRegistry
from ragkit.settings import Settings
from tests.conftest import FakeProvider
from tests.test_api import COMPOST_Q, build, create_kb, sse_events


def _sse(*events: dict[str, Any]) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def _claude(handler: Any) -> AnthropicProvider:
    client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return AnthropicProvider("sk-test", "https://api.anthropic.com", 30, http_client=client)


def _reply(text: str, stop_reason: str = "end_turn") -> bytes:
    usage = {"input_tokens": 42, "output_tokens": 0}
    message = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": []}
    message |= {"stop_reason": None, "stop_sequence": None, "usage": usage}
    return _sse(
        {"type": "message_start", "message": message},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 7},
        },
        {"type": "message_stop"},
    )


async def test_claude_streams_answers_with_the_system_prompt_apart() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(
            200, content=_reply("Staff get 25 days [S1]."), headers={"content-type": "text/event-stream"}
        )

    provider = _claude(handler)
    options = ChatOptions(model="claude-opus-5-5", max_tokens=800)
    messages = [{"role": "system", "content": "Answer from the sources."}, {"role": "user", "content": "Leave?"}]
    answer = "".join([token async for token in provider.chat(messages, options)])
    assert answer == "Staff get 25 days [S1]."
    assert (options.usage.prompt_tokens, options.usage.completion_tokens) == (42, 7)
    body = sent[0]
    assert body["system"] == "Answer from the sources." and body["messages"] == [{"role": "user", "content": "Leave?"}]
    assert "temperature" not in body and body["output_config"] == {"effort": "low"}
    assert body["fallbacks"] == "default"  # a policy decline is retried on a fallback model by the API
    with pytest.raises(ProviderError, match="embedding"):
        await provider.embed("claude-opus-5-5", ["x"], "query")


async def test_claude_errors_are_plain_and_refusals_are_not_answers() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=_reply("", "refusal"), headers={"content-type": "text/event-stream"})

    with pytest.raises(ProviderError, match="declined"):
        _ = [t async for t in _claude(refuse).chat([{"role": "user", "content": "q"}], ChatOptions("claude-haiku-4-5"))]

    def bad_key(request: httpx2.Request) -> httpx2.Response:
        error = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
        return httpx2.Response(401, json=error)

    with pytest.raises(ProviderError, match="rejected the API key"):
        await _claude(bad_key).list_models()

    def models(request: httpx2.Request) -> httpx2.Response:
        data = [{"type": "model", "id": i, "display_name": i, "created_at": "2026-01-01T00:00:00Z"}
                for i in ("claude-sonnet-5-5", "claude-opus-5-5")]  # fmt: skip
        return httpx2.Response(200, json={"data": data, "has_more": False, "first_id": None, "last_id": None})

    assert await _claude(models).list_models() == ["claude-opus-5-5", "claude-sonnet-5-5"]


def test_keys_are_encrypted_and_unreadable_with_another_secret(settings: Settings) -> None:
    token = connections.encrypt(settings, "sk-ant-secret-1234")
    assert "secret" not in token and connections.decrypt(settings, token) == "sk-ant-secret-1234"
    other = settings.model_copy(update={"state_dir": settings.state_dir.parent / "elsewhere"})
    assert connections.decrypt(other, token) is None
    assert connections.masked("sk-ant-secret-1234") == "…1234"


@pytest.fixture
def app(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The real registry; only the network call behind a new connection is faked."""
    fake = FakeProvider(models=["claude-opus-5-5", "claude-sonnet-5-5"])

    def build(self: ProviderRegistry, kind: str, base_url: str, api_key: str | None) -> FakeProvider:
        if api_key == "wrong":
            raise_on = FakeProvider()
            raise_on.list_models = _fail  # type: ignore[method-assign]
            return raise_on
        return fake

    monkeypatch.setattr(ProviderRegistry, "build", build)
    with TestClient(create_app(settings, in_process_worker=True)) as client:
        yield client


async def _fail() -> list[str]:
    raise ProviderError("Claude rejected the API key. Add the connection again with a valid key.")


def test_add_test_use_and_remove_a_connection(app: TestClient, corpus: Path) -> None:
    assert [c["id"] for c in app.get("/api/connections").json()] == ["fastembed", "ollama"]
    assert app.post("/api/connections", json={"kind": "anthropic"}).status_code == 422  # Claude needs a key
    wrong = app.post("/api/connections", json={"kind": "anthropic", "api_key": "wrong"})
    assert wrong.status_code == 422 and "rejected the API key" in wrong.json()["detail"]
    added = app.post("/api/connections", json={"kind": "anthropic", "api_key": "sk-ant-abcdefgh1234"})
    assert added.status_code == 201, added.text
    claude = added.json()["connection"]
    assert added.json()["models"] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert claude["name"] == "Claude" and claude["key_hint"] == "…1234" and claude["readable"]
    assert "sk-ant" not in app.get("/api/connections").text  # the key never leaves the server
    stored = app.app.state.context.db.get_connection(claude["id"])
    assert stored["api_key"] and "abcdefgh" not in stored["api_key"]
    second = app.post("/api/connections", json={"kind": "anthropic", "api_key": "sk-ant-zzzzzzzz9999"})
    assert second.json()["connection"]["name"] == "Claude 2"
    options = app.get("/api/setup/models").json()["answer_options"]  # offered when creating a chatbot
    assert {"provider": claude["id"], "name": "Claude", "model": "claude-opus-5-5"} in options

    kb = create_kb(app, corpus, embedding={"provider": "fastembed", "model": "BAAI/bge-small-en-v1.5"},
                   generation={"provider": claude["id"], "model": "claude-opus-5-5"})  # fmt: skip
    assert app.get("/api/models", params={"provider": claude["id"]}).json()["models"][0] == "claude-opus-5-5"
    busy = app.delete(f"/api/connections/{claude['id']}")
    assert busy.status_code == 409 and "sample" in busy.json()["detail"]
    app.delete(f"/api/kbs/{kb['id']}")
    assert app.delete(f"/api/connections/{claude['id']}").status_code == 204
    assert app.delete(f"/api/connections/{claude['id']}").status_code == 404


def test_only_owners_change_connections(app: TestClient) -> None:
    app.post("/api/auth/owner", json={"email": "ada@x.io", "password": "correct horse battery"})
    token = app.post("/api/users", json={"email": "eve@x.io", "role": "editor"}).json()["invite_token"]
    editor = TestClient(app.app)
    editor.post(f"/api/auth/invite/{token}", json={"password": "correct horse battery"})
    assert editor.get("/api/connections").status_code == 200
    assert editor.post("/api/connections", json={"kind": "ollama"}).status_code == 403


def test_a_removed_or_unreadable_connection_explains_itself(settings: Settings) -> None:
    from ragkit.storage import Database

    db = Database(settings.db_path)
    registry = ProviderRegistry(settings, db)
    with pytest.raises(ProviderError, match="no longer exists"):
        registry.get("conn_gone")
    connection = db.create_connection("anthropic", "Claude", "https://api.anthropic.com", "not-a-token", "…1234")
    with pytest.raises(ProviderError, match="add it again"):
        registry.get(connection)


def test_chat_runs_on_the_connection_a_chatbot_names(app: TestClient, corpus: Path) -> None:
    local = app.post("/api/connections", json={"kind": "compatible", "base_url": "http://gpu-box:8000/v1"}).json()
    claude = app.post("/api/connections", json={"kind": "anthropic", "api_key": "sk-ant-abcdefgh1234"}).json()
    kb = create_kb(app, corpus, embedding={"provider": local["connection"]["id"], "model": "claude-opus-5-5"},
                   generation={"provider": claude["connection"]["id"], "model": "claude-opus-5-5"},
                   rerank={"enabled": False})  # fmt: skip
    assert build(app, kb["id"])["status"] == "completed"
    events = sse_events(app.post(f"/api/kbs/{kb['id']}/chat", json={"query": COMPOST_Q}).text)
    answer = "".join(event["text"] for event in events if event["type"] == "token")
    assert events[-1]["type"] == "done" and "[S1]" in answer
