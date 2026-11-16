"""Answer feedback (signed-in and shared pages) and the knowledge gaps it feeds."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ragkit.accounts import required_role
from ragkit.api import create_app, knowledge_gaps
from ragkit.settings import Settings
from tests.conftest import FakeRegistry
from tests.test_api import COMPOST_Q, build, chat, create_kb, sse_events

OFF_TOPIC = "Who won the football world cup in 1998?"


def test_feedback_and_gaps(settings: Settings, registry: FakeRegistry, corpus: Path) -> None:
    with TestClient(create_app(settings, providers=registry, in_process_worker=True)) as client:  # type: ignore[arg-type]
        kb = create_kb(client, corpus)
        other = create_kb(client, corpus, name="other")
        assert build(client, kb["id"])["status"] == "completed"
        answered = chat(client, kb["id"], COMPOST_Q)[-1]
        assert answered["outcome"] == "answered"
        for query in (OFF_TOPIC, "who won the football World Cup in 1998"):
            assert chat(client, kb["id"], query)[-1]["outcome"] == "abstained"

        rate = f"/api/kbs/{kb['id']}/feedback"
        assert client.post(rate, json={"trace_id": answered["trace_id"], "rating": "down"}).status_code == 204
        assert client.post(rate, json={"trace_id": "missing", "rating": "up"}).status_code == 404
        wrong_kb = f"/api/kbs/{other['id']}/feedback"  # an answer can only be rated through its own chatbot
        assert client.post(wrong_kb, json={"trace_id": answered["trace_id"], "rating": "up"}).status_code == 404
        assert client.post(rate, json={"trace_id": answered["trace_id"], "rating": "meh"}).status_code == 422

        gaps = client.get("/api/gaps").json()
        assert [(g["question"], g["asked"], g["unanswered"], g["unhelpful"]) for g in gaps] == [
            ("who won the football World Cup in 1998", 2, 2, 0),  # same question, different wording; latest kept
            (COMPOST_Q, 1, 0, 1),
        ]
        assert gaps[0]["kb_id"] == kb["id"]

        assert client.post(rate, json={"trace_id": answered["trace_id"], "rating": None}).status_code == 204
        assert len(client.get("/api/gaps").json()) == 1  # taking the rating back removes the gap

        token = client.post(f"/api/kbs/{kb['id']}/share", json={"enabled": True}).json()["share_token"]
        visitor = TestClient(client.app)
        reply = sse_events(visitor.post(f"/api/share/{token}/chat", json={"query": COMPOST_Q}).text)[-1]
        shared_rate = f"/api/share/{token}/feedback"
        assert visitor.post(shared_rate, json={"trace_id": reply["trace_id"], "rating": "up"}).status_code == 204
        assert client.get(f"/api/traces/{reply['trace_id']}").json()["feedback"] == 1


def test_viewers_may_rate_but_not_read_gaps() -> None:
    assert required_role("POST", "/api/kbs/kb_1/feedback") == "viewer"
    assert required_role("GET", "/api/gaps") == "editor"


def test_gaps_rank_by_count_then_recency() -> None:
    rows = [
        {"kb_id": "a", "query": "Late one?", "outcome": "abstained", "feedback": None, "created_at": 3.0},
        {"kb_id": "a", "query": "early one", "outcome": "abstained", "feedback": None, "created_at": 2.0},
        {"kb_id": "b", "query": "Late one", "outcome": "answered", "feedback": -1, "created_at": 1.0},
    ]
    assert [(g["kb_id"], g["question"]) for g in knowledge_gaps(rows)] == [("a", "Late one?"), ("a", "early one"),
                                                                          ("b", "Late one")]  # fmt: skip
    assert knowledge_gaps(rows, top=1)[0]["last_asked"] == 3.0
