"""Source groups: a chatbot also searches other chatbots, and a local router picks which ones per question."""

from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ragkit.router import Profile, profile_of, route
from tests.test_api import COMPOST_Q, build, client, create_kb, sse_events  # noqa: F401


def _profile(passages: int, **terms: int) -> Profile:
    return Profile(passages, Counter(terms))


def test_router_picks_a_clear_winner_and_searches_several_when_unsure() -> None:
    profiles = {
        "HR": _profile(100, leave=40, salary=20, policy=30),
        "Engineering": _profile(100, deploy=50, ticket=40, policy=10),
    }
    assert route("How many days of leave do I get?", profiles).chosen == ["HR"]
    assert route("How do I deploy a ticket fix?", profiles).chosen == ["Engineering"]
    assert route("What does Engineering say about salary?", profiles).chosen == ["Engineering"]  # named
    unsure = route("Where is the policy?", _profile_pair())
    assert sorted(unsure.chosen) == ["Engineering", "HR"] and unsure.reason.startswith("unsure")
    assert route("Bonjour tout le monde", profiles).chosen == ["HR", "Engineering"]  # no known words: all
    assert route("anything", {"Only": _profile(1)}).chosen == ["Only"]
    tiny = {"Big": _profile(1000, leave=100), "Tiny": _profile(3, bike=2), "Empty": _profile(0)}
    assert route("leave rules", tiny).chosen == ["Big"]  # a group can't win on words it lacks


def _profile_pair() -> dict[str, Profile]:
    return {"HR": _profile(100, policy=30), "Engineering": _profile(100, policy=25)}


def test_profiles_count_each_word_once_per_passage() -> None:
    profile = profile_of([{"title": "Leave", "text": "leave leave salary"}, {"text": "Leave policy"}])
    assert profile.passages == 2 and profile.terms["leave"] == 2 and profile.terms["salary"] == 1


def _event(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(e for e in events if e["type"] == kind)


def test_a_front_door_answers_from_the_right_group(client: TestClient, corpus: Path) -> None:  # noqa: F811
    garden, work = corpus.parent / "garden", corpus.parent / "work"
    garden.mkdir()
    work.mkdir()
    shutil.copy(corpus / "home-composting-guide.md", garden)
    for name in ("remote-team-handbook.md", "bicycle-maintenance-basics.md"):
        shutil.copy(corpus / name, work)
    gardening = create_kb(client, garden, name="Garden")
    working = create_kb(client, work, name="Work")
    for kb in (gardening, working):
        assert build(client, kb["id"])["status"] == "completed"

    door = create_kb(client, corpus, name="Front door", source={"type": "uploads"})  # no documents of its own
    config = client.get(f"/api/kbs/{door['id']}").json()["config"]
    config["retrieval"]["groups"] = [door["id"]]
    assert client.post(f"/api/kbs/{door['id']}/configs", json={"config": config}).status_code == 422  # not itself
    config["retrieval"]["groups"] = [gardening["id"], working["id"]]
    assert client.post(f"/api/kbs/{door['id']}/configs", json={"config": config}).status_code == 201
    assert build(client, door["id"])["status"] == "completed"  # no files of its own, yet ready to ask
    assert client.get(f"/api/kbs/{door['id']}").json()["status"] == "ready"

    events = sse_events(client.post(f"/api/kbs/{door['id']}/chat", json={"query": COMPOST_Q}).text)
    done = events[-1]
    assert done["type"] == "done" and done["outcome"] == "answered", events
    assert {source["group"] for source in _event(events, "sources")["sources"]} == {"Garden"}
    decisions = client.get(f"/api/traces/{done['trace_id']}").json()["data"]["decisions"]
    assert any(d.startswith("routed: Garden is the likely group") for d in decisions), decisions

    named = sse_events(
        client.post(f"/api/kbs/{door['id']}/chat", json={"query": "What does Work say about chains?"}).text
    )
    decisions = client.get(f"/api/traces/{named[-1]['trace_id']}").json()["data"]["decisions"]
    assert "routed: the question names Work" in decisions

    client.delete(f"/api/kbs/{gardening['id']}")  # a removed group is skipped, not an error
    events = sse_events(client.post(f"/api/kbs/{door['id']}/chat", json={"query": "How do I oil a chain?"}).text)
    assert events[-1]["type"] == "done"
