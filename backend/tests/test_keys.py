"""Scoped API keys (H2): hashed, shown once, limited to a chatbot and to asking or managing."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from ragkit.accounts import key_allows
from ragkit.api import create_app
from ragkit.settings import Settings
from tests.conftest import FakeRegistry
from tests.test_api import COMPOST_Q, build, create_kb

ADMIN = {"Authorization": "Bearer s3cret"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_scope_rules() -> None:
    assert key_allows("POST", "/api/kbs/kb_a/chat", "kb_a") and key_allows("GET", "/api/kbs/kb_a", "kb_a")
    assert key_allows("GET", "/api/schema", "kb_a") and key_allows("GET", "/api/system/ready", "kb_a")
    for method, path in [
        ("POST", "/api/kbs/kb_b/chat"),
        ("GET", "/api/kbs/kb_ab"),  # a chatbot whose id starts the same
        ("GET", "/api/kbs"),
        ("GET", "/api/traces"),
        ("GET", "/api/jobs/job_1"),
        ("GET", "/api/system/folders"),
        ("POST", "/api/schema"),
    ]:
        assert not key_allows(method, path, "kb_a"), path


def test_a_wrong_key_is_refused_even_on_an_open_install(settings: Settings, registry: FakeRegistry) -> None:
    with TestClient(create_app(settings, providers=registry)) as client:  # type: ignore[arg-type]
        assert client.get("/api/kbs").status_code == 200  # no accounts yet: this machine is the owner
        assert client.get("/api/kbs", headers=bearer("rk_revoked")).status_code == 401


def test_keys_are_scoped_hashed_and_revocable(settings: Settings, registry: FakeRegistry, corpus: Path) -> None:
    secured = settings.model_copy(update={"api_key": "s3cret"})
    with TestClient(create_app(secured, providers=registry, in_process_worker=True)) as admin:  # type: ignore[arg-type]
        admin.headers.update(ADMIN)
        first, second = create_kb(admin, corpus, name="first"), create_kb(admin, corpus, name="second")
        assert build(admin, first["id"])["status"] == "completed"
        assert build(admin, second["id"])["status"] == "completed"

        def make(name: str, kb_id: str | None, scope: str) -> str:
            response = admin.post("/api/keys", json={"name": name, "kb_id": kb_id, "scope": scope})
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["token"].startswith("rk_") and body["key"]["prefix"] == body["token"][:10]
            return str(body["token"])

        ask_first = make("Website widget", first["id"], "ask")
        manage_first = make("CI pipeline", first["id"], "manage")
        ask_all = make("Help desk app", None, "ask")
        assert admin.post("/api/keys", json={"name": "x", "kb_id": "kb_missing"}).status_code == 404
        listed = admin.get("/api/keys").json()
        assert [k["name"] for k in listed] == ["Website widget", "CI pipeline", "Help desk app"]
        assert all("token" not in k and "token_hash" not in k for k in listed)  # never shown again

        visitor = TestClient(admin.app)
        chat = {"query": COMPOST_Q}
        assert visitor.post(f"/api/kbs/{first['id']}/chat", json=chat, headers=bearer(ask_first)).status_code == 200
        assert visitor.get(f"/api/kbs/{first['id']}", headers=bearer(ask_first)).status_code == 200
        assert visitor.post(f"/api/kbs/{first['id']}/builds", headers=bearer(ask_first)).status_code == 403  # asks only
        assert visitor.post(f"/api/kbs/{second['id']}/chat", json=chat, headers=bearer(ask_first)).status_code == 403
        assert visitor.get("/api/kbs", headers=bearer(ask_first)).status_code == 403  # can't list other chatbots
        assert visitor.get("/api/traces", headers=bearer(ask_first)).status_code == 403

        assert visitor.post(f"/api/kbs/{first['id']}/builds", headers=bearer(manage_first)).status_code == 202
        assert visitor.post(f"/api/kbs/{second['id']}/builds", headers=bearer(manage_first)).status_code == 403
        assert visitor.delete(f"/api/kbs/{first['id']}", headers=bearer(manage_first)).status_code == 403  # owners only
        assert visitor.get("/api/keys", headers=bearer(manage_first)).status_code == 403  # keys can't make keys

        assert visitor.post(f"/api/kbs/{second['id']}/chat", json=chat, headers=bearer(ask_all)).status_code == 200
        assert visitor.get("/api/kbs", headers=bearer(ask_all)).status_code == 200
        assert visitor.post(f"/api/kbs/{second['id']}/builds", headers=bearer(ask_all)).status_code == 403

        assert visitor.get("/api/kbs", headers=bearer("rk_made-up")).status_code == 401
        key_id = next(k["id"] for k in admin.get("/api/keys").json() if k["name"] == "Help desk app")
        assert next(k for k in admin.get("/api/keys").json() if k["id"] == key_id)["last_used_at"]
        assert admin.delete(f"/api/keys/{key_id}").status_code == 204
        assert visitor.get("/api/kbs", headers=bearer(ask_all)).status_code == 401  # revoked at once
        actors = {row["actor"] for row in admin.get("/api/audit").json()}
        assert "key CI pipeline" in actors  # what a key changes is in the change history

        deadline = time.time() + 60  # the build the CI key started must finish before the chatbot can go
        while admin.get(f"/api/kbs/{first['id']}").json()["status"] == "building" and time.time() < deadline:
            time.sleep(0.1)
        assert admin.delete(f"/api/kbs/{first['id']}").status_code == 204
        assert [k["name"] for k in admin.get("/api/keys").json()] == []  # a chatbot's keys go with it
