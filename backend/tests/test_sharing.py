"""Shared chatbots and their limits, the folder picker and the setup model check."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ragkit.api import create_app
from ragkit.settings import Settings
from ragkit.storage import Database
from tests.conftest import FakeRegistry
from tests.test_api import COMPOST_Q, build, create_kb, sse_events

ADMIN = {"Authorization": "Bearer s3cret"}


def test_share_link_allows_questions_and_nothing_else(settings: Settings, registry: FakeRegistry, corpus: Path) -> None:
    secured = settings.model_copy(update={"api_key": "s3cret"})
    with TestClient(create_app(secured, providers=registry, in_process_worker=True)) as client:  # type: ignore[arg-type]
        client.headers.update(ADMIN)
        kb = create_kb(client, corpus)
        assert kb["share_token"] is None
        assert build(client, kb["id"])["status"] == "completed"
        token = client.post(f"/api/kbs/{kb['id']}/share", json={"enabled": True}).json()["share_token"]
        again = client.post(f"/api/kbs/{kb['id']}/share", json={"enabled": True}).json()["share_token"]
        assert token and again == token  # switching it on again keeps the same link

        visitor = TestClient(client.app)
        assert visitor.get(f"/api/share/{token}").json() == {"name": "sample", "ready": True, "documents": 3}
        assert visitor.get(f"/api/share/{token}/suggestions").status_code == 200
        reply = visitor.post(f"/api/share/{token}/chat", json={"query": COMPOST_Q, "session_id": "v"})
        assert sse_events(reply.text)[-1]["outcome"] == "answered"
        assert visitor.delete(f"/api/share/{token}/sessions/v").status_code == 204
        assert visitor.get(f"/api/kbs/{kb['id']}").status_code == 401  # the link opens nothing else
        assert visitor.post(f"/api/kbs/{kb['id']}/chat", json={"query": "q"}).status_code == 401

        fresh = client.post(f"/api/kbs/{kb['id']}/share", json={"enabled": True, "reset": True}).json()["share_token"]
        assert fresh != token and visitor.get(f"/api/share/{token}").status_code == 404
        assert client.post(f"/api/kbs/{kb['id']}/share", json={"enabled": False}).json()["share_token"] is None
        assert visitor.get(f"/api/share/{fresh}").status_code == 404


def test_shared_chat_is_rate_limited(settings: Settings, registry: FakeRegistry, corpus: Path) -> None:
    limited = settings.model_copy(update={"share_rate_per_minute": 1})
    with TestClient(create_app(limited, providers=registry)) as client:  # type: ignore[arg-type]
        kb = create_kb(client, corpus)
        token = client.post(f"/api/kbs/{kb['id']}/share", json={"enabled": True}).json()["share_token"]
        assert client.post(f"/api/share/{token}/chat", json={"query": "hello"}).status_code == 200
        blocked = client.post(f"/api/share/{token}/chat", json={"query": "hello"})
        assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"


def test_rate_limits_are_shared_between_processes(tmp_path: Path) -> None:
    first, second = Database(tmp_path / "state.db"), Database(tmp_path / "state.db")  # two API processes
    assert first.rate_allow("a", 2, 60, now=0) and second.rate_allow("a", 2, 60, now=1)
    assert not first.rate_allow("a", 2, 60, now=2) and not second.rate_allow("a", 2, 60, now=2)
    assert second.rate_allow("a", 2, 60, now=60.5)  # the first hit left the window
    first.purge_ephemeral(turns_before=0, hits_before=1000)
    assert first._one("SELECT COUNT(*) AS n FROM rate_hits")["n"] == 0  # type: ignore[index]


def test_shared_chatbots_have_a_daily_ceiling(settings: Settings, registry: FakeRegistry, corpus: Path) -> None:
    capped = settings.model_copy(update={"share_questions_per_day": 2})
    app = create_app(capped, providers=registry)  # type: ignore[arg-type]
    with TestClient(app, client=("8.8.4.4", 1)) as one:
        kb = create_kb(TestClient(app), corpus)
        token = TestClient(app).post(f"/api/kbs/{kb['id']}/share", json={"enabled": True}).json()["share_token"]
        two = TestClient(app, client=("1.1.1.1", 1))  # another visitor
        assert one.post(f"/api/share/{token}/chat", json={"query": "hello"}).status_code == 200
        assert two.post(f"/api/share/{token}/chat", json={"query": "hello"}).status_code == 200
        blocked = two.post(f"/api/share/{token}/chat", json={"query": "hello"})
        assert blocked.status_code == 429 and "today" in blocked.json()["detail"]


def test_folder_picker_stays_inside_data_roots(settings: Settings, registry: FakeRegistry, corpus: Path) -> None:
    (corpus / "nested").mkdir()
    (corpus / ".hidden").mkdir()
    with TestClient(create_app(settings, providers=registry)) as client:  # type: ignore[arg-type]
        top = client.get("/api/system/folders").json()
        assert top["path"] is None and [f["path"] for f in top["folders"]] == [str(corpus.parent.resolve())]
        root = client.get("/api/system/folders", params={"path": top["folders"][0]["path"]}).json()
        assert root["parent"] is None and [(f["name"], f["files"]) for f in root["folders"]] == [("sample", 3)]
        sample = client.get("/api/system/folders", params={"path": str(corpus)}).json()
        assert sample["files"] == 3 and sample["parent"] == str(corpus.parent.resolve())
        assert [f["name"] for f in sample["folders"]] == ["nested"]  # hidden folders are left out
        assert client.get("/api/system/folders", params={"path": str(corpus.parents[1])}).status_code == 422


def test_setup_model_check_offers_the_built_in_search_model(settings: Settings, registry: FakeRegistry) -> None:
    with TestClient(create_app(settings, providers=registry)) as client:  # type: ignore[arg-type]
        ready = client.get("/api/setup/models").json()
        assert ready["embedding"]["ok"] and ready["generation"]["ok"] and ready["fallback_embedding"] is None
        registry.provider.models = ["qwen3.5:9b"]  # the search model is missing; the built-in one is unavailable here
        missing = client.get("/api/setup/models").json()
        assert not missing["embedding"]["ok"] and missing["generation"]["ok"] and missing["installed"] == ["qwen3.5:9b"]
        assert client.get("/api/setup/models", params={"preset": "nope"}).status_code == 404
