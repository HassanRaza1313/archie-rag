"""Claim check: sentence splitting with borrowed citations, windowed scoring, verdicts, and the agent wiring."""

from __future__ import annotations

from typing import Any

from ragkit import claims
from ragkit.config import KnowledgeBaseConfig
from tests.test_agent import COMPOST_Q, ask
from tests.test_agent import world_factory as world_factory


class WordModel:
    """Entailment = every hypothesis word appears in the premise."""

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []

    def entailment(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.pairs.extend(pairs)
        return [1.0 if set(h.lower().split()) <= set(p.lower().split()) else 0.0 for p, h in pairs]


def test_claims_borrow_citations_and_skip_non_claims() -> None:
    answer = (
        "Turn the compost pile every two weeks. Water it when it feels dry [S2].\n"
        "- **Add dry leaves** to an ammonia-smelling pile [S1, S9].\n"
        "Would you like more detail? I don't know the answer to that part. Ok.\n"
        "A claim at the end of a paragraph cites the source after the stop. [S1] And this one has no citation."
    )
    found = claims.claims_of(answer, source_count=2)
    assert [(c.text, c.labels) for c in found] == [
        ("Turn the compost pile every two weeks.", [2]),  # borrows the next sentence's citation
        ("Water it when it feels dry [S2].", [2]),
        ("- **Add dry leaves** to an ammonia-smelling pile [S1, S9].", [1]),  # S9 does not exist
        ("A claim at the end of a paragraph cites the source after the stop. [S1]", [1]),
        ("And this one has no citation.", [1]),  # borrows the previous one's
    ]
    assert claims.hypothesis(found[2].text) == "Add dry leaves to an ammonia-smelling pile ."


def test_check_takes_the_best_window_and_verdict_needs_half() -> None:
    long_passage = " ".join(["filler"] * 300) + " turn the pile every two weeks"
    found = [claims.Claim("turn the pile every two weeks [S1]", [1]), claims.Claim("add meat scraps [S2]", [2])]
    model = WordModel()
    checked = claims.check(model, found, [long_passage, "add dry leaves"])
    assert [c.support for c in checked] == [1.0, 0.0]
    assert len(model.pairs) == 3 and all(len(p.split()) <= claims.WINDOW_WORDS for p, _ in model.pairs)
    assert claims.verdict(checked) == ("pass", "1 of 2 sentences backed by their sources")
    assert claims.verdict(checked[1:])[0] == "fail" and claims.verdict([])[0] == "pass"
    assert claims.windows("short passage") == ["short passage"]


async def test_agent_flags_unsupported_sentences(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory()
    assert KnowledgeBaseConfig().agent.verification == "claims"
    world.registry.provider.script = lambda messages: (
        "Mix about three parts browns to one part greens [S1]. Meat scraps speed the pile up nicely [S1]. "
        "Turn the pile every two weeks to keep it working [S1]."
    )
    world.registry.entailment.unsupported = {"meat"}
    result = await ask(agent, kb_id, COMPOST_Q)
    final = result["final"]
    assert final["verify"] == "pass" and final["verify_reason"] == "2 of 3 sentences backed by their sources"
    assert [c["support"] < claims.SUPPORTED for c in final["claims"]] == [False, True, False]
    assert final["claims"][1]["sources"] == ["S1"] and final["claims"][1]["text"].startswith("Meat scraps")
    trace = world.db.get_trace(final["trace_id"])
    assert trace is not None
    assert "claim check: 2 of 3 sentences backed by their sources" in trace["data"]["decisions"]
    assert next(s for s in trace["data"]["spans"] if s["name"] == "verify")["attributes"]["support"] == 0.667


async def test_agent_abstains_when_most_sentences_are_unsupported(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory(agent={"on_verify_fail": "abstain"})
    world.registry.provider.script = lambda messages: "Meat scraps speed the compost pile up nicely [S1]."
    world.registry.entailment.unsupported = {"meat"}
    result = await ask(agent, kb_id, COMPOST_Q)
    assert result["final"]["outcome"] == "abstained" and "Meat" not in result["answer"]
    assert result["final"]["claims"] == []


async def test_agent_falls_back_to_citations_without_the_model(world_factory: Any) -> None:
    world, kb_id, agent = await world_factory()
    world.registry.entailment = None
    result = await ask(agent, kb_id, COMPOST_Q)
    assert result["final"]["verify"] == "pass" and result["final"]["claims"] == []
    trace = world.db.get_trace(result["final"]["trace_id"])
    assert trace is not None
    assert any(d.startswith("claim check unavailable") for d in trace["data"]["decisions"])
