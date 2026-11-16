"""Golden sets drafted from the documents, kept per chatbot, and re-run after settings changes and rebuilds."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ragkit.api import create_app
from ragkit.evaluation import _question, pick_passages
from ragkit.settings import Settings
from tests.conftest import FakeRegistry
from tests.test_api import build, create_kb


def runs_after(client: TestClient, kb_id: str, count: int) -> list[dict[str, Any]]:
    deadline = time.time() + 30
    while time.time() < deadline:
        runs = client.get(f"/api/kbs/{kb_id}/eval/runs").json()
        if len(runs) >= count:
            return runs
        time.sleep(0.1)
    raise AssertionError(f"expected {count} runs")


def test_draft_keep_and_rerun(settings: Settings, registry: FakeRegistry, corpus: Path) -> None:
    with TestClient(create_app(settings, providers=registry, in_process_worker=True)) as client:  # type: ignore[arg-type]
        kb = create_kb(client, corpus)
        kb_id = kb["id"]
        assert client.post(f"/api/kbs/{kb_id}/golden/draft", json={}).status_code == 409  # not built yet
        assert build(client, kb_id)["status"] == "completed"
        assert client.get(f"/api/kbs/{kb_id}/eval/runs").json() == []  # no saved questions, nothing ran
        assert client.get(f"/api/kbs/{kb_id}/golden").json() == {"items": [], "updated_at": None}

        drafted = client.post(f"/api/kbs/{kb_id}/golden/draft", json={"count": 3}).json()["items"]
        answerable = [item for item in drafted if item["expected_sources"]]
        assert len(answerable) == 3 and all(item["question"].endswith("?") for item in drafted)
        assert len({item["expected_sources"][0] for item in answerable}) == 3  # one per document
        assert [item["question"] for item in drafted if not item["expected_sources"]] == [
            "Who won the 1998 football world cup?",
            "What is the capital of Peru?",
        ]
        assert not any(item["question"].lower().startswith("question:") for item in drafted)

        saved = client.put(f"/api/kbs/{kb_id}/golden", json={"items": drafted}).json()
        assert saved["items"][0]["question"] == drafted[0]["question"] and saved["updated_at"]

        config = client.get(f"/api/kbs/{kb_id}").json()["config"]
        config["agent"]["confidence_threshold"] = 0.3  # no rebuild needed: the set re-runs now
        assert client.post(f"/api/kbs/{kb_id}/configs", json={"config": config}).json()["requires_rebuild"] is False
        first = runs_after(client, kb_id, 1)[0]
        assert first["reason"] == "settings saved (version 2)" and first["items"] == len(drafted)
        assert first["metrics"]["hit_at_5"] is not None and len(first["results"]) == len(drafted)

        manual = client.post(f"/api/kbs/{kb_id}/eval", json={"items": drafted[:1]}).json()
        assert manual["reason"] == "run by hand" and manual["items"] == 1

        assert build(client, kb_id)["status"] == "completed"
        reasons = [run["reason"] for run in runs_after(client, kb_id, 3)]
        assert reasons == ["rebuilt (version 2)", "run by hand", "settings saved (version 2)"]  # newest first


def test_pick_passages_spreads_across_documents() -> None:
    long = "x" * 250
    payloads = [{"document_uri": "a", "ordinal": i, "text": long} for i in range(10)]
    payloads.append({"document_uri": "b", "ordinal": 0, "text": long})
    payloads.append({"document_uri": "c", "ordinal": 0, "text": "short"})
    payloads.append({"document_uri": "d", "ordinal": 0, "text": long, "excluded": True})
    picked = pick_passages(payloads, 4)
    # two per document at most (4 over 2 usable documents), evenly through each, round-robin; short and excluded skipped
    assert [(p["document_uri"], p["ordinal"]) for p in picked] == [("a", 2), ("b", 0), ("a", 7)]
    assert pick_passages([], 3) == []


def test_question_cleanup() -> None:
    assert _question('"Question: How hot does a pile get"\nextra') == "How hot does a pile get?"
    assert _question("Q. Why turn it?") == "Why turn it?"
    assert _question("ok") is None
