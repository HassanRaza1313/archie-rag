"""Accounts: the first owner, invitations, sign-in sessions, roles and the audit log."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ragkit.accounts import check_password, hash_password, required_role
from ragkit.api import create_app
from ragkit.settings import Settings
from tests.conftest import FakeRegistry
from tests.test_api import create_kb

PASSWORD = "correct horse battery"


@pytest.fixture
def app(settings: Settings, registry: FakeRegistry) -> Iterator[TestClient]:
    with TestClient(create_app(settings, providers=registry)) as client:  # type: ignore[arg-type]
        yield client


def person(app: TestClient) -> TestClient:
    """Another browser: same server, its own cookies."""
    return TestClient(app.app)


def invite(owner: TestClient, email: str, role: str) -> tuple[str, str]:
    reply = owner.post("/api/users", json={"email": email, "role": role})
    assert reply.status_code == 201, reply.text
    return reply.json()["user"]["id"], reply.json()["invite_token"]


def join(app: TestClient, token: str, name: str = "") -> TestClient:
    browser = person(app)
    assert browser.get(f"/api/auth/invite/{token}").status_code == 200
    assert browser.post(f"/api/auth/invite/{token}", json={"name": name, "password": PASSWORD}).status_code == 200
    return browser


def test_passwords_and_role_rules() -> None:
    stored = hash_password(PASSWORD)
    assert check_password(PASSWORD, stored) and not check_password("wrong password", stored)
    assert not check_password(PASSWORD, None) and not check_password(PASSWORD, "md5$abc")
    assert required_role("POST", "/api/kbs/kb_1/chat") == "viewer"
    assert required_role("GET", "/api/kbs/kb_1/") == "viewer"
    assert required_role("GET", "/api/kbs/kb_1/documents") == "editor"
    assert required_role("GET", "/api/traces") == "editor"
    assert required_role("POST", "/api/kbs/kb_1/builds") == "editor"
    assert required_role("DELETE", "/api/kbs/kb_1") == "owner"
    assert required_role("GET", "/api/audit") == "owner"
    assert required_role("PATCH", "/api/users/user_1") == "owner"


def test_open_until_the_first_account_then_roles_apply(app: TestClient, corpus: Path) -> None:
    open_me = {"accounts": False, "role": "owner", "via": "open", "user": None, "key_required": False}
    assert app.get("/api/auth/me").json() == open_me
    kb = create_kb(app, corpus)  # open local install: no sign-in yet
    short = app.post("/api/auth/owner", json={"email": "a@x.io", "password": "short"})
    assert short.status_code == 422
    owner = app.post("/api/auth/owner", json={"email": "Ada@X.io", "name": "Ada", "password": PASSWORD})
    assert owner.status_code == 201 and owner.json()["user"]["email"] == "ada@x.io"
    cookie = owner.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert app.post("/api/auth/owner", json={"email": "b@x.io", "password": PASSWORD}).status_code == 409

    stranger = person(app)
    assert stranger.get("/api/kbs").status_code == 401  # accounts are on now
    assert stranger.get("/api/auth/me").json()["role"] is None

    _, viewer_token = invite(app, "vic@x.io", "viewer")
    _, editor_token = invite(app, "eve@x.io", "editor")
    assert app.post("/api/users", json={"email": "VIC@x.io"}).status_code == 409
    viewer, editor = join(app, viewer_token, "Vic"), join(app, editor_token)
    assert person(app).get(f"/api/auth/invite/{viewer_token}").status_code == 404  # links work once

    assert viewer.get("/api/kbs").status_code == 200
    assert viewer.post(f"/api/kbs/{kb['id']}/chat", json={"query": "hello"}).status_code == 200
    assert viewer.post(f"/api/kbs/{kb['id']}/builds").status_code == 403
    assert viewer.get("/api/traces").status_code == 403
    assert editor.post(f"/api/kbs/{kb['id']}/builds").status_code == 202
    assert editor.get("/api/users").status_code == 403
    assert editor.delete(f"/api/kbs/{kb['id']}").status_code == 403

    log = app.get("/api/audit").json()
    actions = [(row["actor"], row["action"]) for row in log]
    assert ("eve@x.io", "POST /api/kbs/{kb_id}/builds") in actions
    assert ("vic@x.io", "joined") in actions and ("ada@x.io", "created the owner account") in actions
    assert not any("chat" in action for _, action in actions)  # asking questions is not an admin change


def test_sign_in_sessions_and_password_changes(app: TestClient) -> None:
    app.post("/api/auth/owner", json={"email": "ada@x.io", "password": PASSWORD})
    browser = person(app)
    assert browser.post("/api/auth/sign-in", json={"email": "ada@x.io", "password": "nope"}).status_code == 401
    assert browser.post("/api/auth/sign-in", json={"email": "who@x.io", "password": PASSWORD}).status_code == 401
    signed = browser.post("/api/auth/sign-in", json={"email": "ADA@x.io", "password": PASSWORD})
    assert signed.status_code == 200 and signed.json()["role"] == "owner"
    assert browser.get("/api/users").status_code == 200

    wrong = browser.post("/api/auth/password", json={"current": "nope", "new": "a brand new password"})
    assert wrong.status_code == 403
    assert browser.post("/api/auth/password", json={"current": PASSWORD, "new": "short"}).status_code == 422
    changed = browser.post("/api/auth/password", json={"current": PASSWORD, "new": "a brand new password"})
    assert changed.status_code == 204
    assert app.get("/api/users").status_code == 401  # other devices were signed out
    assert browser.get("/api/users").status_code == 200  # this one got a fresh session

    assert browser.post("/api/auth/sign-out").status_code == 204
    assert browser.get("/api/users").status_code == 401
    failed = [row for row in app.app.state.context.db.audit_log(50) if row["action"] == "sign-in failed"]
    assert {row["actor"] for row in failed} == {"ada@x.io", "who@x.io"}


def test_sign_in_is_rate_limited(app: TestClient) -> None:
    app.post("/api/auth/owner", json={"email": "ada@x.io", "password": PASSWORD})
    codes = [
        person(app).post("/api/auth/sign-in", json={"email": "ada@x.io", "password": "guess"}).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_owners_cannot_lock_themselves_out(app: TestClient) -> None:
    owner_id = app.post("/api/auth/owner", json={"email": "ada@x.io", "password": PASSWORD}).json()["user"]["id"]
    assert app.patch(f"/api/users/{owner_id}", json={"role": "editor"}).status_code == 409
    assert app.patch(f"/api/users/{owner_id}", json={"disabled": True}).status_code == 409
    assert app.delete(f"/api/users/{owner_id}").status_code == 409

    second_id, token = invite(app, "bo@x.io", "owner")
    bo = join(app, token)
    assert app.patch(f"/api/users/{owner_id}", json={"role": "editor"}).json()["role"] == "editor"
    assert app.get("/api/users").status_code == 403  # Ada is an editor now
    assert bo.patch(f"/api/users/{owner_id}", json={"disabled": True}).json()["disabled"] == 1
    assert app.get("/api/kbs").status_code == 401  # disabling signs the person out everywhere

    reset = bo.post(f"/api/users/{second_id}/invite").json()["invite_token"]
    assert person(app).get(f"/api/auth/invite/{reset}").json() == {"email": "bo@x.io", "joined": True}


def test_open_install_is_open_only_to_local_networks(settings: Settings, registry: FakeRegistry) -> None:
    app = create_app(settings, providers=registry)
    with TestClient(app, client=("8.8.8.8", 4000)) as internet:
        lan = TestClient(app, client=("192.168.1.20", 4000))  # shares the started app
        assert internet.get("/api/kbs").status_code == 401  # e.g. behind the HTTPS profile without accounts or a key
        body = {"email": "eve@x.io", "password": PASSWORD}
        assert internet.post("/api/auth/owner", json=body).status_code == 401
        assert internet.get("/api/auth/me").json()["key_required"] is False
        assert lan.get("/api/kbs").status_code == 200


def test_viewers_do_not_see_share_links(app: TestClient, corpus: Path) -> None:
    kb = create_kb(app, corpus)
    app.post("/api/auth/owner", json={"email": "ada@x.io", "password": PASSWORD})
    assert app.post(f"/api/kbs/{kb['id']}/share", json={"enabled": True}).json()["share_token"]
    _, token = invite(app, "vic@x.io", "viewer")
    viewer = join(app, token)
    assert viewer.get(f"/api/kbs/{kb['id']}").json()["share_token"] is None
    assert viewer.get("/api/kbs").json()[0]["share_token"] is None
    assert app.get("/api/kbs").json()[0]["share_token"]


def test_api_key_still_works_and_guards_the_first_owner(settings: Settings, registry: FakeRegistry) -> None:
    secured = settings.model_copy(update={"api_key": "s3cret"})
    with TestClient(create_app(secured, providers=registry)) as client:  # type: ignore[arg-type]
        body = {"email": "ada@x.io", "password": PASSWORD}
        assert client.post("/api/auth/owner", json=body).status_code == 401
        key = {"Authorization": "Bearer s3cret"}
        assert client.post("/api/auth/owner", json=body, headers=key).status_code == 201
        assert client.get("/api/users", headers=key).status_code == 200  # automation keeps working
