"""Retrieval evaluation on a golden set, per config version.

A golden item names the files that should be retrieved for a question (``expected_sources``); an empty list
marks a question the corpus cannot answer, which the system should abstain on. Items may also name phrases that
must all appear in one retrieved chunk (``expected_text``), which measures parsing and chunking rather than
just finding the right file. Metrics: hit@1/3/5 and MRR over answerable items (per file, and per passage when
phrases are given), false-abstain rate, correct-abstain rate, and retrieval latency. No LLM calls are needed, so
two versions can be compared in seconds.
"""

from __future__ import annotations

import asyncio
import logging
import re
import statistics
import time
from typing import Any

from pydantic import BaseModel, Field

from ragkit.index import VectorIndex
from ragkit.privacy import Redactor
from ragkit.providers import ChatOptions, ProviderRegistry
from ragkit.retrieval import retrieve
from ragkit.storage import Database

logger = logging.getLogger(__name__)


class GoldenItem(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    expected_sources: list[str] = Field(default_factory=list)
    expected_text: list[str] = Field(default_factory=list)


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _contains_all(text: str, phrases: list[str]) -> bool:
    haystack = _normalise(text)
    return all(_normalise(phrase) in haystack for phrase in phrases)


async def evaluate(
    db: Database,
    index: VectorIndex,
    providers: ProviderRegistry,
    kb_id: str,
    items: list[GoldenItem],
    version: int | None = None,
) -> dict[str, Any]:
    kb = db.get_kb(kb_id)
    if kb is None or not kb["active_collection"]:
        raise ValueError("the knowledge base has not been built")
    version, config = db.get_config(kb_id, version)
    warnings = []
    if config.index_fingerprint() != kb["built_index_hash"]:
        warnings.append("this version has index settings that differ from the built index; results use the built index")
    embedder = providers.get(config.embedding.provider)
    reranker = None
    if config.rerank.enabled:
        try:
            reranker = await asyncio.to_thread(providers.reranker, config.rerank.model)
        except Exception as exc:
            warnings.append(f"reranker unavailable ({type(exc).__name__}); scored without it")

    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for item in items:
        started = time.perf_counter()
        vector = None
        if config.retrieval.mode != "keyword":
            vector = (await embedder.embed(config.embedding.model, [item.question], "query"))[0]
        result = await retrieve(
            item.question,
            vector,
            collection=kb["active_collection"],
            config=config,
            index=index,
            reranker=reranker,
            filters=None,
            candidates=config.retrieval.candidates,
        )
        latencies.append((time.perf_counter() - started) * 1000)
        abstained = not result.selected or result.confidence < config.agent.confidence_threshold
        expected = {source.lower() for source in item.expected_sources}
        rank = next(
            (
                position
                for position, candidate in enumerate(result.ranked, start=1)
                if str(candidate.payload.get("file_name", "")).lower() in expected
                or str(candidate.payload.get("document_uri", "")).lower() in expected
            ),
            None,
        )
        passage_rank = None
        if item.expected_text:
            passage_rank = next(
                (
                    position
                    for position, candidate in enumerate(result.ranked, start=1)
                    if _contains_all(candidate.text, item.expected_text)
                ),
                None,
            )
        context = " ".join(candidate.text for candidate in result.selected)
        rows.append(
            {
                "question": item.question,
                "answerable": bool(expected),
                "rank": rank,
                "passage_rank": passage_rank,
                "in_context": bool(item.expected_text) and _contains_all(context, item.expected_text),
                "abstained": abstained,
                "confidence": result.confidence,
            }
        )

    answerable = [row for row in rows if row["answerable"]]
    unanswerable = [row for row in rows if not row["answerable"]]

    with_text = [row for row, item in zip(rows, items, strict=True) if row["answerable"] and item.expected_text]

    def hit_at(k: int, key: str = "rank", pool: list[dict[str, Any]] = answerable) -> float | None:
        return _ratio(sum(1 for row in pool if row[key] and row[key] <= k), len(pool))

    passage = {
        "passage_hit_at_1": hit_at(1, "passage_rank", with_text),
        "passage_hit_at_3": hit_at(3, "passage_rank", with_text),
        "passage_hit_at_5": hit_at(5, "passage_rank", with_text),
        "context_recall": _ratio(sum(row["in_context"] for row in with_text), len(with_text)),
        "passage_mrr": _ratio(sum(1 / row["passage_rank"] for row in with_text if row["passage_rank"]), len(with_text)),
    }

    return {
        "kb_id": kb_id,
        "version": version,
        "items": len(rows),
        "metrics": {
            "hit_at_1": hit_at(1),
            "hit_at_3": hit_at(3),
            "hit_at_5": hit_at(5),
            "mrr": _ratio(sum(1 / row["rank"] for row in answerable if row["rank"]), len(answerable)),
            **(passage if with_text else {}),
            "false_abstain_rate": _ratio(sum(row["abstained"] for row in answerable), len(answerable)),
            "correct_abstain_rate": _ratio(sum(row["abstained"] for row in unanswerable), len(unanswerable)),
            "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else None,
            "latency_p95_ms": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 1) if latencies else None,
        },
        "warnings": warnings,
        "results": rows,
    }


def _ratio(numerator: float, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


# Drafting a golden set from the documents

DRAFT_PROMPT = """Write one question a person might ask that this passage answers. Use your own words rather than \
phrases copied from the passage, and do not mention "the passage" or "the document". Reply with the question only.

Passage:
{text}"""

OUT_OF_SCOPE_PROMPT = """A chatbot answers only from these documents: {titles}.
Write {count} short questions a visitor might ask it that these documents would not answer, one per line, \
with nothing else."""

DRAFT_SAMPLE = 2000  # passages read to choose from
MIN_PASSAGE_CHARS = 200  # shorter passages (headings, captions) make poor test questions


def pick_passages(payloads: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Spread ``count`` passages across documents, evenly through each one, so no single file dominates."""
    documents: dict[str, list[dict[str, Any]]] = {}
    for payload in payloads:
        if not payload.get("excluded") and len(str(payload.get("text", ""))) >= MIN_PASSAGE_CHARS:
            documents.setdefault(str(payload.get("document_uri", "")), []).append(payload)
    if not documents:
        return []
    per_document = -(-count // len(documents))
    spread = []
    for passages in documents.values():
        passages.sort(key=lambda p: p.get("ordinal", 0))
        take = min(per_document, len(passages))
        spread.append([passages[int((i + 0.5) * len(passages) / take)] for i in range(take)])
    picked = [doc[i] for i in range(per_document) for doc in spread if i < len(doc)]  # round-robin over documents
    return picked[:count]


def _question(reply: str) -> str | None:
    line = next((line.strip().strip("\"'*-• ").strip() for line in reply.splitlines() if line.strip()), "")
    line = re.sub(r"^(question|q)\s*[:.]\s*", "", line, flags=re.IGNORECASE)
    if len(line) < 8 or len(line) > 300:
        return None
    return line if line.endswith("?") else line + "?"


async def draft(
    db: Database, index: VectorIndex, providers: ProviderRegistry, kb_id: str, count: int = 10, out_of_scope: int = 2
) -> list[dict[str, Any]]:
    """Test questions drafted from the indexed passages, each naming the file it came from, plus a few questions
    the documents can't answer. Enrichment's questions are used when present; otherwise the chatbot's model writes
    one per passage. People review them before relying on them."""
    kb = db.get_kb(kb_id)
    if kb is None or not kb["active_collection"]:
        raise ValueError("the knowledge base has not been built")
    _, config = db.get_config(kb_id)
    payloads = await asyncio.to_thread(index.sample_payloads, kb["active_collection"], DRAFT_SAMPLE)
    passages = pick_passages(payloads, count)
    if not passages:
        raise ValueError("there are no passages long enough to draft questions from")
    llm = providers.get(config.generation.provider)
    redactor = None
    if config.privacy.redact_prompt == "always" or (config.privacy.redact_prompt == "auto" and llm.is_remote):
        redactor = Redactor(config.privacy.detectors)
    limit = asyncio.Semaphore(4)

    async def ask(prompt: str, max_tokens: int) -> str:
        options = ChatOptions(
            model=config.generation.model,
            temperature=0.3,
            max_tokens=max_tokens,
            context_window=config.generation.context_window,
            thinking=False,
        )
        async with limit:
            return "".join([token async for token in llm.chat([{"role": "user", "content": prompt}], options)])

    async def for_passage(payload: dict[str, Any]) -> str | None:
        enriched = (payload.get("enrichment") or {}).get("questions") or []
        if enriched:
            return _question(str(enriched[0]))
        text = str(payload["text"])[:3000]
        try:
            return _question(await ask(DRAFT_PROMPT.format(text=redactor.redact(text) if redactor else text), 80))
        except Exception as exc:
            logger.warning("drafting a question failed: %s", exc)
            return None

    questions = await asyncio.gather(*(for_passage(p) for p in passages))
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload, question in zip(passages, questions, strict=True):
        if question and _normalise(question) not in seen:
            seen.add(_normalise(question))
            items.append({"question": question, "expected_sources": [str(payload.get("file_name", ""))]})
    if not items:
        raise ValueError("the chatbot's model could not draft questions; check it in Settings › Generation")
    if out_of_scope:
        titles = sorted({str(name) for p in payloads if (name := p.get("title") or p.get("file_name"))})
        try:
            reply = await ask(OUT_OF_SCOPE_PROMPT.format(titles="; ".join(titles[:30]), count=out_of_scope), 120)
            outside = [q for q in map(_question, reply.splitlines()) if q and _normalise(q) not in seen]
            items += [{"question": q, "expected_sources": []} for q in outside[:out_of_scope]]
        except Exception as exc:
            logger.warning("drafting out-of-scope questions failed: %s", exc)
    return items


async def rerun(db: Database, index: VectorIndex, providers: ProviderRegistry, kb_id: str, reason: str) -> None:
    """Run the chatbot's saved golden set and keep the result; nothing to do without one. Never raises."""
    golden = db.get_golden(kb_id)
    if not golden or not golden["items"]:
        return
    try:
        items = [GoldenItem.model_validate(item) for item in golden["items"]]
        db.add_eval_run(kb_id, await evaluate(db, index, providers, kb_id, items), reason)
    except Exception as exc:
        logger.warning("test questions for %s did not run after %s: %s", kb_id, reason, exc)
