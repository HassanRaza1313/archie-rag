from __future__ import annotations

import time
from pathlib import Path

import pytest

from ragkit.cache import AnswerCache, same_meaning_markers, scope_key
from ragkit.config import CacheConfig, KnowledgeBaseConfig
from ragkit.memory import SessionMemory
from ragkit.storage import Database, JobConflictError
from tests.conftest import fake_vector


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "state.db")


def test_kb_versions_and_activation(db: Database) -> None:
    config = KnowledgeBaseConfig()
    kb_id = db.create_kb("books", config)
    assert db.get_kb(kb_id)["active_version"] == 1
    changed = config.model_copy(update={"rerank": config.rerank.model_copy(update={"top_n": 2})})
    assert db.add_config_version(kb_id, changed) == 2
    version, loaded = db.get_config(kb_id)
    assert version == 2 and loaded.rerank.top_n == 2
    assert db.get_config(kb_id, 1)[1].rerank.top_n == config.rerank.top_n
    assert [v["version"] for v in db.list_versions(kb_id)] == [2, 1]
    assert db.activate_index(kb_id, 2, "hash", "kb__a") is None
    assert db.activate_index(kb_id, 2, "hash2", "kb__b") == "kb__a"
    assert db.get_kb_by_name("books")["active_collection"] == "kb__b"
    with pytest.raises(KeyError):
        db.get_config(kb_id, 99)


def test_duplicate_kb_name_fails(db: Database) -> None:
    db.create_kb("books", KnowledgeBaseConfig())
    with pytest.raises(Exception, match="UNIQUE"):
        db.create_kb("books", KnowledgeBaseConfig())


def test_jobs_claim_conflict_cancel_and_resume(db: Database) -> None:
    kb_id = db.create_kb("books", KnowledgeBaseConfig())
    job_id = db.create_job(kb_id, 1, ["validate", "index"])
    with pytest.raises(JobConflictError):
        db.create_job(kb_id, 1, ["validate"])
    claimed = db.claim_job()
    assert claimed["id"] == job_id and claimed["status"] == "running"
    assert db.claim_job() is None
    assert db.claim_job(stale_after_s=-1)["id"] == job_id  # a dead worker's job is resumable
    assert db.heartbeat(job_id) is False
    assert db.request_cancel(job_id) is True and db.heartbeat(job_id) is True
    db.finish_job(job_id, "cancelled", None, {"chunks": 0})
    job = db.get_job(job_id)
    assert job["status"] == "cancelled" and [s["stage"] for s in job["stages"]] == ["validate", "index"]
    queued = db.create_job(kb_id, 1, ["validate"])
    db.request_cancel(queued)
    assert db.get_job(queued)["status"] == "cancelled"


def test_stage_updates_reject_unknown_fields(db: Database) -> None:
    kb_id = db.create_kb("books", KnowledgeBaseConfig())
    job_id = db.create_job(kb_id, 1, ["parse"])
    db.update_stage(job_id, "parse", status="running", items_in=3, detail={"warnings": ["x"]})
    assert db.get_job(job_id)["stages"][0]["detail"] == {"warnings": ["x"]}
    with pytest.raises(ValueError):
        db.update_stage(job_id, "parse", status_code=1)


def test_traces_filter_and_purge(db: Database) -> None:
    kb_id = db.create_kb("books", KnowledgeBaseConfig())
    for index, outcome in enumerate(["answered", "abstained", "answered"]):
        db.insert_trace(
            {
                "id": f"t{index}",
                "kb_id": kb_id,
                "config_version": 1,
                "session_id": None,
                "created_at": 100.0 + index,
                "outcome": outcome,
                "total_ms": 10.0,
                "ttft_ms": None,
                "cache_hit": 0,
                "confidence": 0.5,
                "retries": 0,
                "verify": "pass",
                "query": "q",
                "data": {"spans": []},
            }
        )
    assert [t["id"] for t in db.list_traces(kb_id, None, 10, 0)] == ["t2", "t1", "t0"]
    assert [t["id"] for t in db.list_traces(kb_id, "abstained", 10, 0)] == ["t1"]
    assert db.get_trace("t0")["data"] == {"spans": []}
    assert db.purge_traces(kb_id, older_than=101.5) == 2


def test_answer_cache_exact_semantic_and_guards(db: Database) -> None:
    cache = AnswerCache(db)
    config = CacheConfig(semantic_threshold=0.9)
    scope = scope_key("kb", "index", "query")
    question = "What ratio of browns to greens should compost have?"
    cache.record(scope, question, fake_vector(question), "3 to 1 [S1]", [{"label": "S1"}], config)
    exact = cache.lookup(scope, "what RATIO of browns to greens should compost have", None, config)
    assert exact and exact.match == "exact" and exact.answer == "3 to 1 [S1]"
    paraphrase = "What ratio of browns to greens should my compost have?"
    semantic = cache.lookup(scope, paraphrase, fake_vector(paraphrase), config)
    assert semantic and semantic.match == "semantic"
    negated = "What ratio of browns to greens should compost not have?"
    assert cache.lookup(scope, negated, fake_vector(question), config) is None
    assert cache.lookup(scope_key("kb", "other", "query"), question, None, config) is None
    assert cache.lookup(scope, paraphrase, fake_vector(paraphrase), CacheConfig(semantic=False)) is None


def test_cache_promotes_frequent_questions_and_evicts_rare_ones(db: Database) -> None:
    cache = AnswerCache(db)
    config = CacheConfig(capacity=2, history=3)
    scope = "s"
    for question, asks in (("alpha", 3), ("beta", 1), ("gamma", 2)):
        for _ in range(asks):
            cache.record(scope, question, None, f"answer {question}", [], config)
    assert [row["question"] for row in db.cache_rows(scope, config.capacity)] == ["alpha", "gamma"]
    assert cache.lookup(scope, "beta", None, config) is None  # in history, not in the active cache
    cache.record(scope, "delta", None, "d", [], config)
    assert {row["question"] for row in db.cache_rows(scope, 10)} == {"alpha", "gamma", "delta"}
    for _ in range(3):
        cache.lookup(scope, "gamma", None, config)
    assert db.cache_rows(scope, 1)[0]["question"] == "gamma"
    stats = db.cache_stats("s", capacity=2)
    assert stats["history_size"] == 3 and stats["cache_size"] == 2


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        ("How do I fix a flat tyre?", "How can I fix a flat tyre?", True),
        ("Is compost ready after 2 months?", "Is compost ready after 3 months?", False),
        ("Does it attract pests?", "Does it not attract pests?", False),
        ("What does Alice say about chains?", "What does Bob say about chains?", False),
    ],
)
def test_same_meaning_markers(left: str, right: str, same: bool) -> None:
    assert same_meaning_markers(left, right) is same


def test_memory_is_bounded_by_turns_sessions_and_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = SessionMemory(max_sessions=2, ttl_s=60)
    for index in range(5):
        memory.add_turn("a", f"q{index}", f"a{index}")
    assert memory.turns("a", 2) == [("q3", "a3"), ("q4", "a4")]
    memory.add_turn("b", "q", "a")
    memory.add_turn("c", "q", "a")
    assert len(memory) == 2 and memory.turns("a", 5) == []
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 120)
    assert memory.turns("b", 5) == [] and len(memory) == 0
    memory.add_turn("d", "q", "a")
    memory.clear("d")
    assert memory.turns("d", 5) == []
