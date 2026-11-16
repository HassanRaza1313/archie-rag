"""Connectors against stand-ins of each service: a real SQLite file, a local S3 server, and the documented response
shapes of the Atlassian, Microsoft Graph, Google Drive and Notion APIs (httpx mock transport)."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from ragkit.api import create_app
from ragkit.connectors import Account, Cache, ConnectorError, atlassian, fetch, google, microsoft, notion
from ragkit.connectors.database import read_only_query, row_document
from ragkit.settings import Settings
from tests.conftest import FakeRegistry
from tests.test_api import build


def mock(monkeypatch: pytest.MonkeyPatch, module: Any, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Every HTTP call the connector makes goes to ``handler``."""
    real = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        return real(transport=httpx.MockTransport(handler), follow_redirects=True, **kwargs)

    monkeypatch.setattr(module, "http_client", client)


# The shared cache


def test_cache_downloads_only_changes_and_drops_what_is_gone(tmp_path: Path) -> None:
    loads: list[str] = []

    def loader(name: str) -> Callable[[], bytes]:
        return lambda: loads.append(name) or name.encode()

    cache = Cache(tmp_path)
    first = cache.keep("svc://a", "v1", ".txt", loader("a1"), {"owner": "Sam"})
    cache.keep("svc://b", "v1", ".txt", loader("b1"))
    cache.finish()
    cache = Cache(tmp_path)
    again = cache.keep("svc://a", "v1", ".txt", loader("a-not-loaded"))
    cache.keep("svc://c", "v1", ".txt", loader("c1"))
    cache.finish()
    assert loads == ["a1", "b1", "c1"] and again.path == first.path and again.path.read_text() == "a1"
    assert sorted(p.name.split("-")[0] for p in tmp_path.iterdir() if p.suffix == ".txt") == ["a", "c"]


# Databases


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM articles",
        "SELECT 1; DROP TABLE articles",
        "WITH gone AS (DELETE FROM articles RETURNING *) SELECT * FROM gone",
        "SELECT * INTO copy FROM articles",
        "UPDATE articles SET title = 'x'",
        "",
    ],
)
def test_only_reading_queries_are_accepted(sql: str) -> None:
    with pytest.raises(ConnectorError):
        read_only_query(sql)


def test_reading_queries_pass() -> None:
    assert read_only_query("-- latest\nSELECT id, updated_at, created_by FROM articles;").startswith("SELECT")
    assert read_only_query("with recent as (select * from t) select * from recent").startswith("with")


def test_rows_keep_short_values_as_fields() -> None:
    row_id, markdown, fields = row_document(
        ["id", "title", "status", "body"], (7, "Refunds", "live", "Line one\nLine two")
    )
    assert row_id == "7" and fields == {"id": 7, "title": "Refunds", "status": "live"}
    assert markdown.startswith("# Refunds") and "## body" in markdown and "Line two" in markdown


@pytest.fixture
def articles(tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT, team TEXT, body TEXT)")
        db.executemany(
            "INSERT INTO articles VALUES (?, ?, ?, ?)",
            [
                (1, "Refunds", "support", "Customers can return unused parts within 30 days for a full refund."),
                (2, "Shipping", "logistics", "Orders ship within two working days from the Leeds warehouse."),
            ],
        )
    return path


def test_database_fetch_is_incremental(articles: Path, tmp_path: Path) -> None:
    account = Account("database", {}, {"url": f"sqlite:///{articles.as_posix()}"})
    query = "SELECT id, title, team, body FROM articles ORDER BY id"
    items = fetch("database", account, query, tmp_path / "cache", 100)
    assert [item.fields["team"] for item in items] == ["support", "logistics"]
    assert items[0].uri == "sqlite://app.db/1" and "30 days" in items[0].path.read_text()
    with sqlite3.connect(articles) as db:
        db.execute("UPDATE articles SET body = 'Returns within 60 days.' WHERE id = 1")
        db.execute("DELETE FROM articles WHERE id = 2")
    again = fetch("database", account, query, tmp_path / "cache", 100)
    assert len(again) == 1 and "60 days" in again[0].path.read_text()
    assert len(list((tmp_path / "cache").glob("*.md"))) == 1  # the deleted row's file is gone
    with pytest.raises(ConnectorError, match="query failed"):
        fetch("database", account, "SELECT nope FROM articles", tmp_path / "cache", 100)


def test_database_account_and_build_end_to_end(
    articles: Path, settings: Settings, registry: FakeRegistry, tmp_path: Path
) -> None:
    with TestClient(create_app(settings, providers=registry, in_process_worker=True)) as client:  # type: ignore[arg-type]
        bad = client.post(
            "/api/source-accounts", json={"kind": "database", "secret": {"url": "sqlite:///nowhere/x.db"}}
        )
        assert bad.status_code == 422 and "Couldn't connect" in bad.json()["detail"]
        url = f"sqlite:///{articles.as_posix()}"
        added = client.post(
            "/api/source-accounts", json={"kind": "database", "name": "Help desk", "secret": {"url": url}}
        )
        assert added.status_code == 201, added.text
        account = added.json()["account"]
        assert added.json()["found"] == "connected to sqlite" and "secret" not in account
        listing = client.get("/api/source-accounts").json()
        assert listing["accounts"][0]["name"] == "Help desk" and "secret" not in listing["accounts"][0]
        assert listing["sources"]["jira"]["account"] == "atlassian" and "database" in listing["kinds"]

        source = {
            "type": "database",
            "account": account["id"],
            "location": "SELECT id, title, team, body FROM articles",
        }
        kb = client.post("/api/kbs", json={"name": "Help", "config": {"source": source}}).json()
        config = client.get(f"/api/kbs/{kb['id']}").json()["config"]
        assert client.post(f"/api/kbs/{kb['id']}/configs/validate", json={"config": config}).json()["valid"] is True
        broken = {**config, "source": {**config["source"], "location": "DELETE FROM articles"}}
        check = client.post(f"/api/kbs/{kb['id']}/configs/validate", json={"config": broken}).json()
        assert check["errors"][0]["loc"] == ["source", "location"]
        assert build(client, kb["id"])["status"] == "completed"
        documents = client.get(f"/api/kbs/{kb['id']}/documents").json()
        assert len(documents) == 2
        assert client.delete(f"/api/source-accounts/{account['id']}").status_code == 409  # still in use
        client.delete(f"/api/kbs/{kb['id']}")
        assert not (settings.state_dir / "sources" / kb["id"]).exists()  # downloaded copies go with the chatbot
        assert client.delete(f"/api/source-accounts/{account['id']}").status_code == 204


# S3, through boto3 against a local stand-in


OBJECTS = {"policies/leave.md": b"# Leave\n\nStaff get 25 days of paid leave.", "policies/setup.exe": b"MZ"}


class FakeS3(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        bucket, _, key = parts.path.lstrip("/").partition("/")
        query = parse_qs(parts.query)
        if not bucket:
            body = b"<ListAllMyBucketsResult><Buckets><Bucket><Name>docs</Name></Bucket></Buckets>"
            body += b"</ListAllMyBucketsResult>"
        elif key:
            body = OBJECTS[key]
        else:
            prefix = query.get("prefix", [""])[0]
            contents = "".join(
                f'<Contents><Key>{k}</Key><ETag>"{len(v)}"</ETag><Size>{len(v)}</Size>'
                f"<LastModified>2026-11-01T10:00:00.000Z</LastModified></Contents>"
                for k, v in OBJECTS.items()
                if k.startswith(prefix)
            )
            listing = f"<Name>{bucket}</Name><IsTruncated>false</IsTruncated>{contents}"
            body = f"<ListBucketResult>{listing}</ListBucketResult>".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def s3_endpoint() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_s3_reads_supported_files_under_a_prefix(s3_endpoint: str, tmp_path: Path) -> None:
    from ragkit.connectors import s3

    account = Account(
        "s3",
        {"region": "us-east-1", "endpoint_url": s3_endpoint},
        {"access_key_id": "test", "secret_access_key": "test"},
    )
    assert s3.check(account) == "1 bucket visible"
    items = fetch("s3", account, "docs/policies/", tmp_path, 10)
    assert [item.uri for item in items] == ["s3://docs/policies/leave.md"]  # the .exe is not a supported type
    assert "25 days" in items[0].path.read_text() and items[0].fields["modified"].startswith("2026-11-01")
    with pytest.raises(ConnectorError, match="bucket"):
        fetch("s3", account, "", tmp_path, 10)


# Atlassian


def test_confluence_cloud_pages_and_jira_issues(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base = "https://acme.atlassian.net"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        assert request.headers["Authorization"].startswith("Basic ")
        path = request.url.path
        if path == "/rest/api/2/myself":
            return httpx.Response(200, json={"displayName": "Archie Bot"})
        if path == "/wiki/api/v2/spaces":
            return httpx.Response(200, json={"results": [{"id": "42", "key": "HR"}]})
        if path == "/wiki/api/v2/spaces/42/pages":
            if "cursor" in str(request.url):
                return httpx.Response(
                    200, json={"results": [page(2, "Expenses", "Claim within 30 days.")], "_links": {}}
                )
            return httpx.Response(
                200,
                json={
                    "results": [page(1, "Leave", "<p>25 days of leave.</p>")],
                    "_links": {"next": "/wiki/api/v2/spaces/42/pages?cursor=abc"},
                },
            )
        if path == "/rest/api/3/search/jql":
            assert json.loads(request.content)["jql"] == "project = HELP"
            return httpx.Response(200, json={"issues": [issue()], "isLast": True})
        return httpx.Response(404)

    def page(number: int, title: str, body: str) -> dict[str, Any]:
        return {
            "id": str(number),
            "title": title,
            "body": {"storage": {"value": body}},
            "version": {"number": 3, "createdAt": "2026-10-01T09:00:00Z"},
            "_links": {"webui": f"/spaces/HR/pages/{number}"},
        }

    def issue() -> dict[str, Any]:
        adf = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Printer jams."}]}],
        }
        comment = {
            "author": {"displayName": "Dana"},
            "created": "2026-10-02T10:00:00Z",
            "body": {
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Fixed by resetting it."}]}],
            },
        }
        return {
            "key": "HELP-7",
            "fields": {
                "summary": "Printer jams",
                "description": adf,
                "status": {"name": "Done"},
                "priority": {"name": "High"},
                "labels": ["office"],
                "assignee": {"displayName": "Dana"},
                "updated": "2026-10-02T10:00:00Z",
                "comment": {"comments": [comment]},
            },
        }

    mock(monkeypatch, atlassian, handler)
    account = Account("atlassian", {"base_url": f"{base}/wiki", "email": "bot@acme.com"}, {"token": "t"})
    assert atlassian.check(account) == "signed in as Archie Bot"
    pages = fetch("confluence", account, "HR", tmp_path / "c", 10)
    assert [p.uri for p in pages] == [f"{base}/wiki/spaces/HR/pages/1", f"{base}/wiki/spaces/HR/pages/2"]
    assert "<title>Leave</title>" in pages[0].path.read_text() and pages[0].fields["space"] == "HR"
    issues = fetch("jira", account, "project = HELP", tmp_path / "j", 10)
    assert issues[0].uri == f"{base}/browse/HELP-7"
    assert issues[0].fields == {
        "key": "HELP-7",
        "status": "Done",
        "priority": "High",
        "assignee": "Dana",
        "labels": ["office"],
        "updated": "2026-10-02T10:00:00Z",
    }
    text = issues[0].path.read_text()
    assert "Printer jams." in text and "**Dana** (2026-10-02):" in text and "Fixed by resetting it." in text


def test_jira_data_center_uses_a_bearer_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer pat"
        assert request.url.path == "/jira/rest/api/2/search"
        return httpx.Response(
            200,
            json={
                "issues": [{"key": "OPS-1", "fields": {"summary": "Disk full", "description": "Clear the logs."}}],
                "total": 1,
            },
        )

    mock(monkeypatch, atlassian, handler)
    account = Account("atlassian", {"base_url": "https://intranet.acme.com/jira"}, {"token": "pat"})
    items = fetch("jira", account, "project = OPS", tmp_path, 10)
    assert "Clear the logs." in items[0].path.read_text()


def test_atlassian_errors_are_readable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mock(monkeypatch, atlassian, lambda request: httpx.Response(401))
    account = Account("atlassian", {"base_url": "https://acme.atlassian.net", "email": "x@y.z"}, {"token": "bad"})
    with pytest.raises(ConnectorError, match="refused the credentials"):
        fetch("jira", account, "project = X", tmp_path, 10)


# Microsoft 365


def test_sharepoint_folder_with_subfolders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    site = "https://contoso.sharepoint.com/sites/HR"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.host == "login.microsoftonline.com":
            assert b"client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "graph-token"})
        if request.url.host == "download.example":
            assert "Authorization" not in request.headers  # the pre-authorised link gets no bearer token
            return httpx.Response(200, content=b"Hybrid work: three days in the office.")
        assert request.headers["Authorization"] == "Bearer graph-token"
        if url.endswith("/sites/contoso.sharepoint.com:/sites/HR"):
            return httpx.Response(200, json={"id": "site-1"})
        if url.endswith("/sites/site-1/drives"):
            return httpx.Response(200, json={"value": [{"id": "drive-1", "webUrl": f"{site}/Shared%20Documents"}]})
        if "/drives/drive-1/root:/Policies:/children" in url:
            return httpx.Response(200, json={"value": [{"id": "f1", "name": "Remote", "folder": {}}]})
        if "/drives/drive-1/items/f1/children" in url:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "a",
                            "name": "hybrid.txt",
                            "size": 40,
                            "cTag": "c1",
                            "webUrl": f"{site}/Shared%20Documents/Policies/Remote/hybrid.txt",
                            "@microsoft.graph.downloadUrl": "https://download.example/a",
                            "lastModifiedDateTime": "2026-09-01T08:00:00Z",
                            "lastModifiedBy": {"user": {"displayName": "Priya"}},
                            "parentReference": {"path": "/drive/root:/Policies/Remote"},
                        },
                        {
                            "id": "b",
                            "name": "photo.heic",
                            "size": 9,
                            "@microsoft.graph.downloadUrl": "https://download.example/b",
                        },
                    ]
                },
            )
        return httpx.Response(404, json={})

    mock(monkeypatch, microsoft, handler)
    account = Account("microsoft", {"tenant_id": "t", "client_id": "c"}, {"client_secret": "s"})
    items = fetch("sharepoint", account, f"{site}/Shared Documents/Policies", tmp_path, 10)
    assert len(items) == 1 and "three days" in items[0].path.read_text()
    assert items[0].fields == {"modified": "2026-09-01T08:00:00Z", "modified_by": "Priya", "folder": "/Policies/Remote"}


# Google Drive


def test_google_drive_exports_docs_and_reads_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        .decode()
    )
    key = {
        "type": "service_account",
        "client_email": "archie@proj.iam.gserviceaccount.com",
        "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            assertion = parse_qs(request.content.decode())["assertion"][0]
            assert assertion.count(".") == 2  # header.claims.signature
            return httpx.Response(200, json={"access_token": "drive-token"})
        assert request.headers["Authorization"] == "Bearer drive-token"
        if request.url.path == "/drive/v3/files":
            parent = request.url.params["q"].split("'")[1]
            files = {
                "root123456789": [
                    {"id": "sub", "name": "Sub", "mimeType": "application/vnd.google-apps.folder"},
                    {
                        "id": "doc",
                        "name": "Handbook",
                        "mimeType": "application/vnd.google-apps.document",
                        "version": "5",
                        "webViewLink": "https://docs.google.com/document/d/doc",
                    },
                ],
                "sub": [{"id": "pdf", "name": "notes.txt", "mimeType": "text/plain", "size": "20", "version": "2"}],
            }[parent]
            return httpx.Response(200, json={"files": files})
        if request.url.path == "/drive/v3/files/doc/export":
            assert request.url.params["mimeType"] == "text/markdown"
            return httpx.Response(200, content=b"# Handbook\n\nCore hours are 10 to 4.")
        if request.url.path == "/drive/v3/files/pdf":
            return httpx.Response(200, content=b"Parking is free.")
        return httpx.Response(404)

    mock(monkeypatch, google, handler)
    account = Account("google", {}, {"service_account_json": json.dumps(key)})
    items = fetch("google_drive", account, "https://drive.google.com/drive/folders/root123456789", tmp_path, 10)
    assert sorted(item.path.suffix for item in items) == [".md", ".txt"]
    assert any("Core hours" in item.path.read_text() for item in items)
    with pytest.raises(ConnectorError, match="service account"):
        fetch("google_drive", Account("google", {}, {"service_account_json": "{}"}), "root123456789", tmp_path, 10)


# Notion


def test_notion_database_rows_keep_properties(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database = "a" * 32
    row = {
        "object": "page",
        "id": "row-1",
        "url": "https://www.notion.so/Leave-row1",
        "last_edited_time": "2026-10-01",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Leave policy"}]},
            "Team": {"type": "select", "select": {"name": "People"}},
            "Tags": {"type": "multi_select", "multi_select": [{"name": "hr"}, {"name": "policy"}]},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Notion-Version"] == notion.VERSION
        path = request.url.path
        if path.startswith("/v1/databases/") and request.method == "GET":
            return httpx.Response(200, json={"object": "database"})
        if path.endswith("/query"):
            return httpx.Response(200, json={"results": [row], "has_more": False})
        if path == "/v1/blocks/row-1/children":
            return httpx.Response(
                200,
                json={
                    "has_more": False,
                    "results": [
                        {"id": "b1", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Allowance"}]}},
                        {
                            "id": "b2",
                            "type": "paragraph",
                            "paragraph": {"rich_text": [{"plain_text": "25 days a year."}]},
                        },
                    ],
                },
            )
        return httpx.Response(404)

    mock(monkeypatch, notion, handler)
    items = fetch(
        "notion",
        Account("notion", {}, {"token": "secret"}),
        f"https://www.notion.so/acme/Policies-{database}?v=1",
        tmp_path,
        10,
    )
    assert items[0].fields == {"team": "People", "tags": ["hr", "policy"]}
    text = items[0].path.read_text()
    assert text.startswith("# Leave policy") and "## Allowance" in text and "25 days a year." in text
