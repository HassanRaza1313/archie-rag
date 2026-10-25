"""HTTP API end to end through the real app and its in-process worker (embedded Qdrant, fake models)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ragkit.api import create_app, summarize_traces
from ragkit.settings import Settings
from tests.conftest import GOLDEN_FILE, FakeRegistry

COMPOST_Q = "What ratio of browns to greens should a compost pile have?"


@pytest.fixture
def client(settings: Settings, registry: FakeRegistry) -> Iterator[TestClient]:
    app = create_app(settings, providers=registry, in_process_worker=True)  # type: ignore[arg-type]
    with TestClient(app) as test_client:
        yield test_client


def sse_events(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.strip().split("\n\n"):
        data = next(line[6:] for line in block.splitlines() if line.startswith("data: "))
        events.append(json.loads(data))
    return events


def create_kb(client: TestClient, corpus: Path, name: str = "sample", **config: Any) -> dict[str, Any]:
    response = client.post("/api/kbs", json={"name": name, "config": {"source": {"path": str(corpus)}, **config}})
    assert response.status_code == 201, response.text
    return response.json()


def build(client: TestClient, kb_id: str, timeout: float = 60) -> dict[str, Any]:
    response = client.post(f"/api/kbs/{kb_id}/builds")
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
    raise AssertionError("build did not finish")


def chat(client: TestClient, kb_id: str, query: str, **body: Any) -> list[dict[str, Any]]:
    response = client.post(f"/api/kbs/{kb_id}/chat", json={"query": query, **body})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return sse_events(response.text)


def test_system_endpoints(client: TestClient) -> None:
    assert client.get("/api/system/health").json()["status"] == "ok"
    assert client.get("/api/system/ready").json() == {"ready": True, "qdrant": True, "ollama": True}
    capabilities = client.get("/api/system/capabilities").json()
    assert capabilities["qdrant"]["mode"] == "embedded" and capabilities["qdrant"]["hnsw"] is False
    schema = client.get("/api/schema").json()
    assert "ChunkingConfig" in schema["schema"]["$defs"] and schema["index_stages"][0] == "source"
    assert {p["name"] for p in schema["presets"]} == {"balanced", "high_accuracy", "low_latency"}
    assert client.get("/api/presets/low_latency").json()["retrieval"]["candidates"] == 12
    assert client.get("/api/presets/nope").status_code == 404
    assert client.get("/api/models").json()["models"] == ["nomic-embed-text:latest", "qwen3.5:9b"]


def test_full_lifecycle(client: TestClient, corpus: Path) -> None:
    kb = create_kb(client, corpus)
    assert kb["status"] == "draft"
    assert client.post("/api/kbs", json={"name": "sample"}).status_code == 409

    job = build(client, kb["id"])
    assert job["status"] == "completed", job
    detail = client.get(f"/api/kbs/{kb['id']}").json()
    assert detail["status"] == "ready" and detail["documents"] == 3 and detail["chunks"] > 0

    events = chat(client, kb["id"], COMPOST_Q, session_id="abc")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "status" and "sources" in kinds and kinds[-1] == "done"
    done = events[-1]
    assert done["outcome"] == "answered" and done["citations"]

    traces = client.get("/api/traces", params={"kb_id": kb["id"]}).json()
    assert traces[0]["id"] == done["trace_id"]
    trace = client.get(f"/api/traces/{done['trace_id']}").json()
    assert trace["data"]["funnel"]["context"] > 0
    stats = client.get(f"/api/kbs/{kb['id']}/stats").json()
    assert stats["requests"] == 1 and stats["abstain_rate"] == 0.0

    assert client.get(f"/api/kbs/{kb['id']}/builds").json()[0]["status"] == "completed"
    assert client.delete(f"/api/kbs/{kb['id']}/sessions/abc").status_code == 204
    assert client.delete(f"/api/kbs/{kb['id']}").status_code == 204
    assert client.get(f"/api/kbs/{kb['id']}").status_code == 404


def test_config_versions_and_stale_status(client: TestClient, corpus: Path) -> None:
    kb = create_kb(client, corpus)
    build(client, kb["id"])
    config = client.get(f"/api/kbs/{kb['id']}").json()["config"]
    config["rerank"]["top_n"] = 3
    saved = client.post(f"/api/kbs/{kb['id']}/configs", json={"config": config}).json()
    assert saved == {"version": 2, "requires_rebuild": False}
    assert client.get(f"/api/kbs/{kb['id']}").json()["status"] == "ready"
    config["chunking"]["chunk_size"] = 256
    assert client.post(f"/api/kbs/{kb['id']}/configs", json={"config": config}).json()["requires_rebuild"] is True
    assert client.get(f"/api/kbs/{kb['id']}").json()["status"] == "stale"
    assert [v["version"] for v in client.get(f"/api/kbs/{kb['id']}/configs").json()] == [3, 2, 1]
    assert client.get(f"/api/kbs/{kb['id']}/configs/1").json()["rerank"]["top_n"] == 6
    assert client.get(f"/api/kbs/{kb['id']}/configs/9").status_code == 404
    bad = client.post(f"/api/kbs/{kb['id']}/configs", json={"config": {"chunking": {"chunk_size": 1}}})
    assert bad.status_code == 422


def test_validation_reports_checks(client: TestClient, corpus: Path, registry: FakeRegistry) -> None:
    kb = create_kb(client, corpus)
    config = client.get(f"/api/kbs/{kb['id']}").json()["config"]
    result = client.post(f"/api/kbs/{kb['id']}/configs/validate", json={"config": config}).json()
    assert result["valid"] is True and result["checks"]["source"]["files"] == 3
    assert any(w["stage"] == "index" and "brute force" in w["msg"] for w in result["warnings"])
    registry.provider.models = ["qwen3.5:9b"]
    config["source"]["path"] = str(corpus.parent.parent / "elsewhere")
    result = client.post(f"/api/kbs/{kb['id']}/configs/validate", json={"config": config}).json()
    assert result["valid"] is False
    assert {tuple(e["loc"]) for e in result["errors"]} == {("source", "path"), ("embedding", "model")}
    invalid = client.post(f"/api/kbs/{kb['id']}/configs/validate", json={"config": {"rerank": {"top_n": 0}}}).json()
    assert invalid["valid"] is False and invalid["errors"][0]["loc"] == ["rerank", "top_n"]


def test_build_conflict_and_job_events(client: TestClient, corpus: Path) -> None:
    kb = create_kb(client, corpus)
    first = client.post(f"/api/kbs/{kb['id']}/builds").json()["job_id"]
    second = client.post(f"/api/kbs/{kb['id']}/builds")
    assert second.status_code in (202, 409)
    if second.status_code == 202:  # the first build already finished
        first = second.json()["job_id"]
    stream = client.get(f"/api/jobs/{first}/events")
    events = sse_events(stream.text)
    assert events[-1]["status"] == "completed" and events[-1]["stages"][-1]["stage"] == "activate"
    assert client.get("/api/jobs/job_missing").status_code == 404
    assert client.post("/api/jobs/job_missing/cancel").status_code == 404


def test_chat_input_limits_and_filters(client: TestClient, corpus: Path) -> None:
    kb = create_kb(client, corpus)
    build(client, kb["id"])
    assert client.post(f"/api/kbs/{kb['id']}/chat", json={"query": ""}).status_code == 422
    assert client.post(f"/api/kbs/{kb['id']}/chat", json={"query": "x" * 4001}).status_code == 422
    blocked = client.post(f"/api/kbs/{kb['id']}/chat", json={"query": "q", "filters": {"secret": "1"}})
    assert blocked.status_code == 422 and "cannot filter" in blocked.text
    events = chat(client, kb["id"], COMPOST_Q, filters={"file_name": "home-composting-guide.md"})
    assert {s["file"] for s in next(e for e in events if e["type"] == "sources")["sources"]} == {
        "home-composting-guide.md"
    }
    assert client.post("/api/kbs/kb_missing/chat", json={"query": "q"}).status_code == 404


def test_uploads_source(client: TestClient, settings: Settings) -> None:
    kb = client.post("/api/kbs", json={"name": "uploaded", "config": {"source": {"type": "uploads"}}}).json()
    content = b"# Notes\n\nCompost needs air and water.\n"
    files = [("files", ("notes.md", content, "text/markdown"))]
    assert client.post(f"/api/kbs/{kb['id']}/uploads", files=files).status_code == 201
    assert client.get(f"/api/kbs/{kb['id']}/uploads").json() == [{"name": "notes.md", "bytes": len(content)}]
    bad = [("files", ("evil.exe", b"MZ", "application/octet-stream"))]
    assert client.post(f"/api/kbs/{kb['id']}/uploads", files=bad).status_code == 422
    traversal = [("files", ("../../escape.md", b"# x", "text/markdown"))]
    assert client.post(f"/api/kbs/{kb['id']}/uploads", files=traversal).status_code == 201
    assert not (settings.state_dir / "escape.md").exists()
    assert build(client, kb["id"])["status"] == "completed"


def test_upload_size_limit(settings: Settings, registry: FakeRegistry) -> None:
    limited = settings.model_copy(update={"max_upload_mb": 0})
    with TestClient(create_app(limited, providers=registry)) as test_client:  # type: ignore[arg-type]
        kb = test_client.post("/api/kbs", json={"name": "u", "config": {"source": {"type": "uploads"}}}).json()
        files = [("files", ("big.md", b"x" * 10, "text/markdown"))]
        assert test_client.post(f"/api/kbs/{kb['id']}/uploads", files=files).status_code == 413


def test_uploads_require_uploads_source(client: TestClient, corpus: Path) -> None:
    kb = create_kb(client, corpus)
    files = [("files", ("notes.md", b"# Notes", "text/markdown"))]
    assert client.post(f"/api/kbs/{kb['id']}/uploads", files=files).status_code == 409


def test_evaluation_endpoint(client: TestClient, corpus: Path) -> None:
    kb = create_kb(client, corpus)
    build(client, kb["id"])
    items = [json.loads(line) for line in GOLDEN_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = client.post(f"/api/kbs/{kb['id']}/eval", json={"items": items}).json()
    metrics = report["metrics"]
    assert report["items"] == len(items)
    assert metrics["hit_at_5"] >= 0.9 and metrics["mrr"] >= 0.7
    assert metrics["correct_abstain_rate"] == 1.0 and metrics["false_abstain_rate"] <= 0.2
    unbuilt = create_kb(client, corpus, name="unbuilt")
    assert client.post(f"/api/kbs/{unbuilt['id']}/eval", json={"items": items}).status_code == 409


def test_api_key_is_enforced(settings: Settings, registry: FakeRegistry) -> None:
    secured = settings.model_copy(update={"api_key": "s3cret"})
    with TestClient(create_app(secured, providers=registry)) as test_client:  # type: ignore[arg-type]
        assert test_client.get("/api/system/health").status_code == 200
        assert test_client.get("/api/kbs").status_code == 401
        assert test_client.get("/api/kbs", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert test_client.get("/api/kbs", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_serves_built_ui(settings: Settings, registry: FakeRegistry, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    with TestClient(create_app(settings.model_copy(update={"ui_dist_dir": dist}), providers=registry)) as test_client:  # type: ignore[arg-type]
        assert test_client.get("/").text == "<html>app</html>"
        assert test_client.get("/chatbots/kb_1/chat").text == "<html>app</html>"
        assert test_client.get("/assets/app.js").text == "console.log(1)"
        assert test_client.get("/favicon.svg").text == "<svg/>"
        assert test_client.get("/api/nope").status_code == 404


def test_summarize_traces() -> None:
    rows = [
        {"outcome": "answered", "total_ms": 100.0, "ttft_ms": 50.0, "cache_hit": 0, "verify": "pass"},
        {"outcome": "cache_hit", "total_ms": 10.0, "ttft_ms": None, "cache_hit": 1, "verify": None},
        {"outcome": "abstained", "total_ms": 300.0, "ttft_ms": None, "cache_hit": 0, "verify": "fail"},
    ]
    stats = summarize_traces(rows)
    assert stats["requests"] == 3 and stats["latency_p50_ms"] == 100.0 and stats["cache_hit_rate"] == 0.3333
    assert summarize_traces([]) == {"requests": 0}


def test_suggestions_endpoint(client: TestClient, corpus: Path) -> None:
    kb = create_kb(client, corpus)
    assert client.get(f"/api/kbs/{kb['id']}/suggestions").json() == []
    build(client, kb["id"])
    questions = client.get(f"/api/kbs/{kb['id']}/suggestions").json()
    assert 1 <= len(questions) <= 3 and all(q.startswith("What does ") for q in questions)
