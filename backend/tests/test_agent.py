"""Agent scenarios end to end: real chunking, embedded Qdrant, fake models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ragkit.agent import (
    Agent,
    ChatRequest,
    cited_labels,
    is_referential,
    is_smalltalk,
    looks_multi_part,
    verify_citations,
)
from ragkit.cache import AnswerCache
from ragkit.memory import SessionMemory
from ragkit.providers import ProviderError
from tests.conftest import FakeRegistry
from tests.test_build import World

COMPOST_Q = "What ratio of browns to greens should a compost pile have?"


@pytest.fixture
async def built(world_factory: Any) -> tuple[World, str, Agent]:
    return await world_factory()


@pytest.fixture
def world_factory(settings: Any, registry: FakeRegistry, corpus: Path) -> Any:
    async def make(**overrides: Any) -> tuple[World, str, Agent]:
        world = World(settings, registry)
        kb_id = world.create(corpus, **overrides)
        job = await world.build(kb_id)
        assert job["status"] == "completed", job
        agent = Agent(world.db, world.index, registry, AnswerCache(world.db), SessionMemory())  # type: ignore[arg-type]
        return world, kb_id, agent

    return make


async def ask(agent: Agent, kb_id: str, query: str, **kwargs: Any) -> dict[str, Any]:
    events = [event async for event in agent.run(kb_id, ChatRequest(query, **kwargs))]
    answer = "".join(e["text"] for e in events if e["type"] == "token").strip()
    final = events[-1]
    return {
        "events": events,
        "answer": answer,
        "final": final,
        "sources": next((e["sources"] for e in events if e["type"] == "sources"), []),
    }


def trace_of(world: World, result: dict[str, Any]) -> dict[str, Any]:
    trace = world.db.get_trace(result["final"]["trace_id"])
    assert trace is not None
    return trace


async def test_answers_with_citations_and_trace(built: tuple[World, str, Agent]) -> None:
    world, kb_id, agent = built
    result = await ask(agent, kb_id, COMPOST_Q, session_id="s1")
    assert result["final"]["type"] == "done" and result["final"]["outcome"] == "answered"
    assert result["final"]["verify"] == "pass"
    assert result["sources"][0]["file"] == "home-composting-guide.md"
    assert result["final"]["citations"][0]["label"] == "S1"
    trace = trace_of(world, result)
    assert trace["outcome"] == "answered" and trace["confidence"] >= 0.35
    funnel = trace["data"]["funnel"]
    assert funnel["fused"] >= funnel["relevant"] >= funnel["deduplicated"] >= funnel["context"] > 0
    assert [s["name"] for s in trace["data"]["spans"]] == ["embed", "cache", "retrieve", "generate", "verify"]
    assert trace["data"]["prompt"] is None and "text" not in trace["data"]["candidates"][0]
    prompt = world.registry.provider.chat_calls[-1][-1]["content"]
    assert "[S1] home-composting-guide.md" in prompt and COMPOST_Q in prompt


async def test_repeated_question_is_served_from_cache(built: tuple[World, str, Agent]) -> None:
    world, kb_id, agent = built
    first = await ask(agent, kb_id, COMPOST_Q)
    calls = len(world.registry.provider.chat_calls)
    second = await ask(agent, kb_id, COMPOST_Q.upper())
    assert second["final"]["outcome"] == "cache_hit" and second["answer"] == first["answer"]
    assert len(world.registry.provider.chat_calls) == calls
    assert trace_of(world, second)["cache_hit"] == 1


async def test_unrelated_question_abstains_after_retry(built: tuple[World, str, Agent]) -> None:
    world, kb_id, agent = built
    result = await ask(agent, kb_id, "Who won the football world cup in 1998?")
    assert result["final"]["outcome"] == "abstained"
    assert result["answer"] == "I do not have enough information to answer this question."
    trace = trace_of(world, result)
    assert trace["retries"] == 1
    assert any(d.startswith("retry 1: rewrite") for d in trace["data"]["decisions"])
    assert all(s.get("closest") for s in result["sources"])


async def test_broaden_strategy_drops_filters(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory(agent={"strategies": ["broaden"], "max_reformulations": 1})
    result = await ask(agent, kb_id, COMPOST_Q, filters={"file_name": "remote-team-handbook.md"})
    trace = trace_of(world, result)
    assert any("broaden" in d for d in trace["data"]["decisions"])
    assert result["final"]["outcome"] == "answered"
    assert result["sources"][0]["file"] == "home-composting-guide.md"


async def test_no_retries_when_disabled(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory(agent={"max_reformulations": 0})
    result = await ask(agent, kb_id, "Who won the football world cup in 1998?")
    assert result["final"]["outcome"] == "abstained" and trace_of(world, result)["retries"] == 0


async def test_follow_up_is_condensed_and_bypasses_cache(built: tuple[World, str, Agent]) -> None:
    world, kb_id, agent = built
    await ask(agent, kb_id, COMPOST_Q, session_id="s")
    result = await ask(agent, kb_id, "why does it smell of ammonia?", session_id="s")
    trace = trace_of(world, result)
    assert "condensed follow-up into a standalone question" in trace["data"]["decisions"]
    assert "cache bypassed: follow-up" in trace["data"]["decisions"]
    condense_prompt = world.registry.provider.chat_calls[-2][-1]["content"]
    assert condense_prompt.startswith("Rewrite the follow-up") and COMPOST_Q in condense_prompt


async def test_smalltalk_skips_retrieval(built: tuple[World, str, Agent]) -> None:
    world, kb_id, agent = built
    result = await ask(agent, kb_id, "hello!", profile={"name": "Sam"})
    assert result["answer"].startswith("Hi Sam!") and result["final"]["outcome"] == "answered"
    assert world.registry.provider.chat_calls == []


async def test_personalised_requests_bypass_cache_and_reach_prompt(built: tuple[World, str, Agent]) -> None:
    world, kb_id, agent = built
    await ask(agent, kb_id, COMPOST_Q)
    result = await ask(agent, kb_id, COMPOST_Q, profile={"name": "Sam", "preferences": "short answers"})
    assert result["final"]["outcome"] == "answered"
    assert "Sam" in world.registry.provider.chat_calls[-1][-1]["content"]


async def test_verification_failure_flags_or_abstains(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory()
    world.registry.provider.script = lambda messages: "The ratio is three to one, which is a widely cited figure."
    flagged = await ask(agent, kb_id, COMPOST_Q)
    assert flagged["final"]["verify"] == "fail" and flagged["final"]["outcome"] == "answered"
    _, config = world.db.get_config(kb_id)
    world.db.add_config_version(
        kb_id, config.model_copy(update={"agent": config.agent.model_copy(update={"on_verify_fail": "abstain"})})
    )
    abstained = await ask(agent, kb_id, COMPOST_Q + " ")
    assert abstained["final"]["outcome"] == "abstained"
    tokens = [e for e in abstained["events"] if e["type"] == "token"]
    assert len(tokens) == 1 and "three to one" not in abstained["answer"]


async def test_llm_judge_verification(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory(agent={"verification": "llm"})
    result = await ask(agent, kb_id, COMPOST_Q)
    assert result["final"]["verify"] == "pass"
    assert world.registry.provider.chat_calls[-1][-1]["content"].startswith("Do the sources support")


async def test_decomposition_retrieves_each_part(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory(agent={"strategies": ["rewrite", "decompose"]})
    world.registry.provider.script = lambda messages: (
        "What ratio of browns to greens should compost have?\nWhat tyre pressure should a mountain bike use?"
        if messages[-1]["content"].startswith("Split")
        else "Answer [S1] and [S2] cover both parts in detail here."
    )
    result = await ask(agent, kb_id, "Compare compost ratios versus mountain bike tyre pressure")
    trace = trace_of(world, result)
    assert "decomposed into 2 sub-questions" in trace["data"]["decisions"]
    files = {s["file"] for s in result["sources"]}
    assert {"home-composting-guide.md", "bicycle-maintenance-basics.md"} <= files


async def test_remote_model_gets_redacted_sources(world_factory: Any, corpus: Path) -> None:
    (corpus / "contacts.md").write_text(
        "# Compost contacts\n\nCompost questions about browns and greens ratio go to jane@example.com any time.\n",
        encoding="utf-8",
    )
    world, kb_id, agent = await world_factory()
    world.registry.provider.is_remote = True
    await ask(agent, kb_id, "Who answers compost questions about browns and greens ratio?")
    prompt = world.registry.provider.chat_calls[-1][-1]["content"]
    assert "jane@example.com" not in prompt and "[REDACTED_EMAIL_1]" in prompt


async def test_provider_failure_yields_error_event_and_trace(built: tuple[World, str, Agent]) -> None:
    world, kb_id, agent = built
    world.registry.provider.fail_chat = ProviderError("model crashed")
    result = await ask(agent, kb_id, COMPOST_Q)
    assert result["final"] == {"type": "error", "message": "model crashed", "trace_id": result["final"]["trace_id"]}
    trace = trace_of(world, result)
    assert trace["outcome"] == "error" and trace["data"]["error"]["stage"] == "generate"


async def test_unbuilt_and_unknown_kb(world_factory: Any, settings: Any, registry: FakeRegistry, corpus: Path) -> None:
    world = World(settings, registry)
    kb_id = world.create(corpus)
    agent = Agent(world.db, world.index, registry, AnswerCache(world.db), SessionMemory())  # type: ignore[arg-type]
    result = await ask(agent, kb_id, COMPOST_Q)
    assert result["final"]["type"] == "error" and "not been built" in result["final"]["message"]
    missing = await ask(agent, "kb_missing", COMPOST_Q)
    assert missing["final"] == {"type": "error", "message": "knowledge base not found"}


async def test_works_without_reranker_and_in_keyword_mode(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory(retrieval={"mode": "keyword"})
    world.registry.rerank_enabled = False
    result = await ask(agent, kb_id, COMPOST_Q)
    assert result["final"]["outcome"] == "answered"
    assert result["sources"][0]["file"] == "home-composting-guide.md"


async def test_minimal_telemetry_stores_no_trace(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory(telemetry={"level": "minimal"})
    result = await ask(agent, kb_id, COMPOST_Q)
    assert world.db.get_trace(result["final"]["trace_id"]) is None


async def test_verbose_telemetry_keeps_prompt_and_text(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory(telemetry={"level": "verbose"})
    trace = trace_of(world, await ask(agent, kb_id, COMPOST_Q))
    assert trace["data"]["prompt"] and trace["data"]["candidates"][0]["text"]


def test_helpers() -> None:
    assert is_smalltalk("Thanks!") and not is_smalltalk("thanks, but what about chains?")
    assert is_referential("why is that?") and is_referential("and pressure?")
    assert not is_referential("What tyre pressure should a road bike use?")
    assert looks_multi_part("What is A? And what is B?") and looks_multi_part("compare A versus B")
    assert cited_labels("x [S1] y [S2, S3] z [S1;S4]") == [1, 2, 3, 1, 4]


@pytest.mark.parametrize(
    ("answer", "sources", "verdict"),
    [
        ("The ratio is three to one by volume for most piles [S1].", 2, "pass"),
        ("The ratio is three to one by volume for most piles [S3].", 2, "fail"),
        ("The ratio is three to one by volume for most piles.", 2, "fail"),
        ("I do not know based on these sources.", 2, "pass"),
        (
            "First claim is long enough to count as a claim [S1]. Second claim is also long enough to count. "
            "Third claim is long enough to count as one too.",
            1,
            "fail",
        ),
    ],
)
def test_verify_citations(answer: str, sources: int, verdict: str) -> None:
    assert verify_citations(answer, sources)[0] == verdict


async def test_done_event_carries_answer_summary(built: tuple[World, str, Agent]) -> None:
    _, kb_id, agent = built
    summary = (await ask(agent, kb_id, COMPOST_Q))["final"]["summary"]
    assert summary["used"] >= 1 and summary["searched"] >= summary["used"] and summary["confidence"] > 0
    abstained = (await ask(agent, kb_id, "Who won the football world cup in 1998?"))["final"]
    assert abstained["summary"]["used"] == 0 and abstained["summary"]["retries"] == 1


def test_suggest_questions_round_robins_documents() -> None:
    from ragkit.agent import suggest_questions

    payloads = [
        {"title": "Compost Guide", "section": "Compost Guide › Greens and browns"},
        {"title": "Compost Guide", "section": "Compost Guide › Greens and browns"},
        {"title": "Compost Guide", "section": "Compost Guide › Temperature"},
        {"title": "Bike Basics", "section": "Bike Basics › Tyre pressure"},
        {"title": "Bike Basics", "section": ""},
        {"title": "Notes", "section": "Notes"},
    ]
    assert suggest_questions(payloads) == [
        "What does Compost Guide say about greens and browns?",
        "What does Bike Basics say about tyre pressure?",
        "What does Compost Guide say about temperature?",
    ]
    assert suggest_questions([]) == []
