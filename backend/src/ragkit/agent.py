"""The bounded agent: a fixed state machine that answers one chat request.

    prepare (condense follow-up) -> route -> embed -> cache -> [decompose] -> retrieve <-> reformulate
        -> abstain | generate -> verify -> respond

Every transition is recorded on the request ``Trace``. LLM calls are capped: condense 1, decompose 1,
rewrite <= max_reformulations, generate 1, judge 1 (only with ``verification=llm``).

``Agent.run`` yields events for the UI stream:
``status`` (stage), ``sources`` (labelled context), ``token`` (answer text), ``verdict``, ``done``, ``error``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from ragkit import claims
from ragkit.cache import AnswerCache, scope_key
from ragkit.config import KnowledgeBaseConfig
from ragkit.index import VectorIndex
from ragkit.privacy import Redactor, redact
from ragkit.providers import ChatOptions, ModelProvider, ProviderError, ProviderRegistry, Reranker
from ragkit.retrieval import Candidate, RetrievalResult, deduplicate, fit_budget, retrieve
from ragkit.router import Profile, profile_of, route
from ragkit.storage import MEMORY_TTL_S, MEMORY_TURNS_KEPT, Database
from ragkit.telemetry import REQUEST_LOGGER, Trace
from ragkit.textutil import estimate_tokens, split_sentences

logger = logging.getLogger(__name__)

Event = dict[str, Any]
PROFILE_SAMPLE = 2000  # passages per group the router learns its vocabulary from

CONDENSE_PROMPT = """Rewrite the follow-up question as a standalone question, using the conversation for context.
Reply with the question only.

Conversation:
{history}

Follow-up question: {question}"""

REWRITE_PROMPT = """A search for the question below found nothing relevant. Rewrite it for a document search: use
different wording, spell out abbreviations, add likely synonyms. Reply with the rewritten query only.

Question: {question}"""

DECOMPOSE_PROMPT = """Split the question into at most 3 self-contained sub-questions, one per line, no numbering.
If it is a single question, repeat it unchanged.

Question: {question}"""

JUDGE_PROMPT = """Do the sources support the answer? Reply with JSON only:
{{"verdict": "supported" | "partially_supported" | "unsupported"}}

Sources:
{sources}

Answer:
{answer}"""

_REFERENTIAL = re.compile(
    r"\b(it|its|they|them|their|this|that|these|those|he|she|him|her|the (first|second|third|last|former|latter)"
    r"|what about|how about|and the|more about|tell me more|why)\b",
    re.IGNORECASE,
)
_SMALLTALK = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening)|who are you|what can you do)\b[\s!.?]*$",
    re.IGNORECASE,
)
_MULTI_PART = re.compile(r";|\b(compare|versus|vs\.?|as well as|and also)\b", re.IGNORECASE)
_CITATION = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")
_DONT_KNOW = re.compile(r"\b(do not|don't|cannot|can't) (know|find|answer)\b", re.IGNORECASE)


@dataclass
class Target:
    """One index a question can search: the chatbot's own, or a group it also searches."""

    kb_id: str
    name: str
    collection: str
    config: KnowledgeBaseConfig  # the group's built settings, with the asking chatbot's ranking and context
    index_hash: str


@dataclass
class ChatRequest:
    query: str
    session_id: str | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, str] = field(default_factory=dict)


def is_referential(question: str) -> bool:
    return bool(_REFERENTIAL.search(question)) or len(question.split()) <= 3


def is_smalltalk(question: str) -> bool:
    return bool(_SMALLTALK.match(question))


def looks_multi_part(question: str) -> bool:
    return question.count("?") >= 2 or bool(_MULTI_PART.search(question))


def cited_labels(answer: str) -> list[int]:
    labels: list[int] = []
    for group in _CITATION.findall(answer):
        labels.extend(int(item.strip()[1:]) for item in re.split(r"[,;]", group))
    return labels


def verify_citations(answer: str, source_count: int) -> tuple[str, str]:
    """Deterministic grounding check: cited labels must exist and most claim sentences must cite one."""
    labels = cited_labels(answer)
    if any(not 1 <= label <= source_count for label in labels):
        return "fail", "cites a source that was not provided"
    if not labels:
        return ("pass", "states the answer is unknown") if _DONT_KNOW.search(answer) else ("fail", "no citations")
    claims = [s for s in split_sentences(answer) if len(s) >= 40 and not s.endswith("?")]
    cited = [s for s in claims if _CITATION.search(s)]
    if claims and len(cited) / len(claims) < 0.5:
        return "fail", f"only {len(cited)} of {len(claims)} claims cite a source"
    return "pass", f"{len(set(labels))} source(s) cited"


def support_of(checked: list[claims.Claim]) -> float | None:
    """Share of checked sentences their sources back; None when nothing was checked."""
    return round(sum(c.support >= claims.SUPPORTED for c in checked) / len(checked), 3) if checked else None


def source_label(index: int, candidate: Candidate) -> dict[str, Any]:
    payload = candidate.payload
    return {
        "label": f"S{index}",
        "chunk_id": candidate.id,
        "group": payload.get("group"),
        "file": payload.get("file_name"),
        "document_uri": payload.get("document_uri"),
        "section": payload.get("section") or None,
        "page_start": payload.get("page_start"),
        "page_end": payload.get("page_end"),
        "relevance": round(candidate.relevance, 3),
        "preview": candidate.text[:400],
        "text": candidate.text,
    }


def format_sources(sources: list[Candidate], redactor: Redactor | None) -> str:
    blocks = []
    for index, candidate in enumerate(sources, start=1):
        payload = candidate.payload
        parts = (payload.get("group"), payload.get("file_name"), payload.get("section"))
        where = " › ".join(part for part in parts if part)
        pages = payload.get("page_start")
        if pages and payload.get("page_end") and payload["page_end"] != pages:
            where += f" (pp. {pages}-{payload['page_end']})"
        elif pages:
            where += f" (p. {pages})"
        text = redactor.redact(candidate.text) if redactor else candidate.text
        blocks.append(f"[S{index}] {where}\n{text}")
    return "\n\n".join(blocks)


def format_profile(profile: dict[str, str]) -> str:
    items = [f"{key}: {value}" for key, value in profile.items() if value]
    return f"\nAbout the user (personalise tone, not facts): {'; '.join(items)}\n" if items else ""


def answer_summary(trace: Trace) -> dict[str, Any]:
    """The one-line account shown under an answer: how much was searched, kept and how sure Archie was."""
    return {
        "searched": trace.funnel.get("fused", 0),
        "used": trace.funnel.get("context", 0),
        "confidence": trace.confidence,
        "retries": trace.retries,
        "seconds": round(trace.elapsed_ms() / 1000, 2),
    }


def suggest_questions(payloads: list[dict[str, Any]], limit: int = 3) -> list[str]:
    """Starter questions from section headings, one document at a time, for the empty chat."""
    by_document: dict[str, list[str]] = {}
    for payload in payloads:
        title = str(payload.get("title") or payload.get("file_name") or "").strip()
        section = str(payload.get("section") or "").split(" › ")[-1].strip()
        if not title or not section or section == title or len(section) > 60:
            continue
        sections = by_document.setdefault(title, [])
        if section not in sections:
            sections.append(section)
    questions: list[str] = []
    for depth in range(max((len(s) for s in by_document.values()), default=0)):
        for title, sections in by_document.items():
            if depth < len(sections) and len(questions) < limit:
                questions.append(f"What does {title} say about {sections[depth].lower()}?")
    return questions


class Agent:
    def __init__(
        self,
        db: Database,
        index: VectorIndex,
        providers: ProviderRegistry,
        cache: AnswerCache,
        observer: Callable[[Trace], None] | None = None,
    ) -> None:
        self.db = db
        self.index = index
        self.providers = providers
        self.cache = cache
        self.observer = observer
        self._profiles: dict[str, Profile] = {}  # by collection; a rebuild makes a new collection

    async def run(self, kb_id: str, request: ChatRequest) -> AsyncIterator[Event]:
        kb = await asyncio.to_thread(self.db.get_kb, kb_id)
        if kb is None:
            yield {"type": "error", "message": "knowledge base not found"}
            return
        version, config = await asyncio.to_thread(self.db.get_config, kb_id)
        trace = Trace(
            kb_id,
            version,
            redact(request.query, config.privacy.detectors),
            request.session_id,
            level=config.telemetry.level,
        )
        targets = await asyncio.to_thread(self._targets, kb, config, trace)
        if not targets:
            trace.outcome = "error"
            trace.error = {"stage": "prepare", "type": "NotBuilt", "message": "build the knowledge base first"}
            message = "This chatbot has not been built yet."
            if config.retrieval.groups:
                message = "Build this chatbot, or the chatbots it searches, first."
            yield {"type": "error", "message": message, "trace_id": trace.id}
            self._finish(trace)
            return
        current_stage = "prepare"
        try:
            async for event in self._answer(kb, config, request, trace, targets):
                if event["type"] == "status":
                    current_stage = event["stage"]
                yield event
        except ProviderError as exc:
            trace.outcome = "error"
            trace.error = {"stage": current_stage, "type": "ProviderError", "message": str(exc)}
            message = "Archie couldn't reach its model server. Try again shortly; details are in Activity."
            yield {"type": "error", "message": message, "trace_id": trace.id}
        except Exception as exc:
            logger.exception("chat failed at %s", current_stage)
            trace.outcome = "error"
            trace.error = {"stage": current_stage, "type": type(exc).__name__, "message": str(exc)[:500]}
            yield {"type": "error", "message": "Something went wrong; see the trace for details.", "trace_id": trace.id}
        finally:
            self._finish(trace)

    def _targets(self, kb: dict[str, Any], config: KnowledgeBaseConfig, trace: Trace) -> list[Target]:
        targets = []
        own = kb["active_collection"]
        if own and not (config.retrieval.groups and not self.db.collection_stats(own).get("chunks")):
            targets.append(Target(kb["id"], kb["name"], kb["active_collection"], config, kb["built_index_hash"] or ""))
        for group_id in dict.fromkeys(config.retrieval.groups):
            group = self.db.get_kb(group_id) if group_id != kb["id"] else None
            if group is None or not group["active_collection"] or group["built_version"] is None:
                trace.decide(f"skipped {group['name'] if group else group_id}: not built or no longer exists")
                continue
            _, built = self.db.get_config(group_id, group["built_version"])
            # The group's own search settings fit its index; ranking and context follow the asking chatbot.
            merged = built.model_copy(update={"rerank": config.rerank, "context": config.context})
            collection, index_hash = group["active_collection"], group["built_index_hash"]
            targets.append(Target(group_id, group["name"], collection, merged, index_hash))
        return targets

    async def _profile(self, target: Target) -> Profile:
        if target.collection not in self._profiles:
            if len(self._profiles) > 64:
                self._profiles.clear()
            payloads = await asyncio.to_thread(self.index.sample_payloads, target.collection, PROFILE_SAMPLE)
            self._profiles[target.collection] = profile_of(payloads)
        return self._profiles[target.collection]

    async def _embed(
        self, config: KnowledgeBaseConfig, queries: list[str], memo: dict[tuple[str, ...], list[list[float] | None]]
    ) -> list[list[float] | None]:
        """Query vectors in this config's search model; each model embeds each query once per request."""
        key = (config.embedding.provider, config.embedding.model, *queries)
        if key not in memo:
            provider = self.providers.get(config.embedding.provider)
            memo[key] = list(await provider.embed(config.embedding.model, queries, "query"))
        return memo[key]

    def _finish(self, trace: Trace) -> None:
        if trace.level != "minimal":
            self.db.insert_trace(trace.to_record())
        REQUEST_LOGGER.info(trace.log_line())
        if self.observer:
            self.observer(trace)

    async def _answer(
        self, kb: dict[str, Any], config: KnowledgeBaseConfig, request: ChatRequest, trace: Trace, targets: list[Target]
    ) -> AsyncIterator[Event]:
        llm = self.providers.get(config.generation.provider)
        session_id = f"{kb['id']}:{request.session_id}" if request.session_id else None  # one chatbot's memory only
        history = (
            await asyncio.to_thread(self.db.turns, session_id, config.cache.memory_turns, time.time() - MEMORY_TTL_S)
            if config.cache.memory and session_id
            else []
        )
        question = request.query.strip()

        condensed = False
        if history and is_referential(question):
            yield {"type": "status", "stage": "condense"}
            with trace.span("condense"):
                transcript = "\n".join(f"User: {q}\nAssistant: {a[:600]}" for q, a in history)
                question = (
                    await self._complete(llm, config, CONDENSE_PROMPT.format(history=transcript, question=question))
                    or question
                )
            condensed = True
            trace.decide("condensed follow-up into a standalone question")

        if is_smalltalk(question):
            trace.decide("small talk: answered without searching")
            name = request.profile.get("name")
            answer = (
                f"Hi{' ' + name if name else ''}! Ask me anything about the documents in "
                f"'{kb['name']}' and I will answer with sources."
            )
            trace.outcome = "answered"
            yield {"type": "token", "text": answer}
            await self._remember(session_id, request.query, answer)
            yield {"type": "done", "outcome": "answered", "citations": [], "trace_id": trace.id}
            return
        trace.decide("searched the documents")

        memo: dict[tuple[str, ...], list[list[float] | None]] = {}
        vector = None
        if config.cache.enabled and config.cache.semantic:
            yield {"type": "status", "stage": "embed"}
            with trace.span("embed"):
                vector = (await self._embed(config, [question], memo))[0]

        personalised = any(request.profile.values())
        index_hash = kb["built_index_hash"] or ""
        if config.retrieval.groups:  # an answer also depends on every group's index
            index_hash = hashlib.sha256("|".join(f"{t.kb_id}:{t.index_hash}" for t in targets).encode()).hexdigest()
        scope = scope_key(kb["id"], index_hash, config.query_fingerprint())
        cacheable = config.cache.enabled and not condensed and not personalised and not request.filters
        if config.cache.enabled and not cacheable:
            reason = "follow-up" if condensed else "personalised request" if personalised else "filtered request"
            trace.decide(f"cache bypassed: {reason}")
        if cacheable:
            with trace.span("cache") as attributes:
                hit = await asyncio.to_thread(self.cache.lookup, scope, question, vector, config.cache)
                attributes["hit"] = bool(hit)
            if hit:
                trace.cache_hit, trace.outcome = True, "cache_hit"
                trace.decide(f"cache hit ({hit.match}, similarity {hit.similarity:.2f})")
                yield {"type": "sources", "sources": hit.citations}
                yield {"type": "token", "text": hit.answer}
                await self._remember(session_id, request.query, hit.answer)
                yield {"type": "done", "outcome": "cache_hit", "citations": hit.citations, "trace_id": trace.id}
                return

        queries = [question]
        if "decompose" in config.agent.strategies and looks_multi_part(question):
            yield {"type": "status", "stage": "decompose"}
            with trace.span("decompose"):
                lines = (await self._complete(llm, config, DECOMPOSE_PROMPT.format(question=question))).splitlines()
            sub_questions = [line.strip(" -•\t") for line in lines if line.strip(" -•\t")][:3]
            if len(sub_questions) > 1:
                queries = sub_questions
                trace.decide(f"decomposed into {len(sub_questions)} sub-questions")

        routed = targets
        if len(targets) > 1 and config.retrieval.routing == "auto":
            yield {"type": "status", "stage": "route"}
            with trace.span("route") as attributes:
                profiles = {t.name: await self._profile(t) for t in targets}
                decision = route(question, profiles)
                attributes["groups"] = decision.chosen
            routed = [t for t in targets if t.name in decision.chosen]
            trace.decide(f"routed: {decision.reason}")
        elif len(targets) > 1:
            trace.decide(f"searched all {len(targets)} groups")

        reranker = await self._reranker(config, trace)
        candidates, filters = config.retrieval.candidates, request.filters or None
        retry_strategies = [s for s in config.agent.strategies if s != "decompose"]
        attempt = 0
        while True:
            dense = [t for t in routed if t.config.retrieval.mode != "keyword"]
            vectors: dict[str, list[list[float] | None]] = {t.kb_id: [None] * len(queries) for t in routed}
            missing = any((t.config.embedding.provider, t.config.embedding.model, *queries) not in memo for t in dense)
            if missing:
                yield {"type": "status", "stage": "embed"}
            with trace.span("embed") if missing else contextlib.nullcontext():
                for t in dense:
                    vectors[t.kb_id] = await self._embed(t.config, queries, memo)
            yield {"type": "status", "stage": "retrieve"}
            with trace.span("retrieve", attempt=attempt) as attributes:
                result = await self._retrieve_all(queries, vectors, routed, config, reranker, filters, candidates)
                attributes.update(result.funnel, confidence=result.confidence)
            confident = bool(result.selected) and result.confidence >= config.agent.confidence_threshold
            if confident or attempt >= config.agent.max_reformulations or not retry_strategies:
                break
            strategy = retry_strategies[attempt % len(retry_strategies)]
            attempt += 1
            reason = "no results" if not result.selected else f"confidence {result.confidence:.2f}"
            trace.decide(f"retry {attempt}: {strategy} ({reason})")
            yield {"type": "status", "stage": f"reformulate:{strategy}"}
            if strategy == "rewrite":
                with trace.span("rewrite"):
                    rewritten = await self._complete(llm, config, REWRITE_PROMPT.format(question=question))
                queries = [rewritten or question]
            else:  # broaden: more candidates, no filters, every group
                candidates, filters, routed = candidates * 2, None, targets

        trace.funnel, trace.confidence, trace.retries = result.funnel, result.confidence, attempt
        trace.candidates = [c.summary(rank) for rank, c in enumerate(result.ranked[:10], start=1)]

        if not result.selected or result.confidence < config.agent.confidence_threshold:
            trace.outcome = "abstained"
            trace.decide("abstained: " + ("no relevant chunks" if not result.selected else "low confidence"))
            closest = [source_label(i, c) | {"closest": True} for i, c in enumerate(result.ranked[:3], start=1)]
            answer = config.agent.abstain_message
            yield {"type": "sources", "sources": closest}
            yield {"type": "token", "text": answer}
            await self._remember(session_id, request.query, answer)
            yield {
                "type": "done",
                "outcome": "abstained",
                "citations": closest,
                "summary": answer_summary(trace),
                "trace_id": trace.id,
            }
            return

        sources = [source_label(i, c) for i, c in enumerate(result.selected, start=1)]
        yield {"type": "sources", "sources": sources}
        redact_prompt = config.privacy.redact_prompt == "always" or (
            config.privacy.redact_prompt == "auto" and llm.is_remote
        )
        if redact_prompt:
            trace.decide("sources redacted for a remote model")
        prompt = config.generation.prompt_template.format(
            profile=format_profile(request.profile),
            sources=format_sources(result.selected, Redactor(config.privacy.detectors) if redact_prompt else None),
            question=question,
        )
        trace.prompt = prompt
        buffered = config.agent.verification != "off" and config.agent.on_verify_fail == "abstain"
        yield {"type": "status", "stage": "generate"}
        options = self._chat_options(config)
        parts: list[str] = []
        with trace.span("generate") as attributes:
            messages = [
                {"role": "system", "content": config.generation.system_prompt},
                {"role": "user", "content": prompt},
            ]
            async for token in llm.chat(messages, options):
                if trace.ttft_ms is None:
                    trace.ttft_ms = trace.elapsed_ms()
                parts.append(token)
                if not buffered:
                    yield {"type": "token", "text": token}
            attributes.update(
                prompt_tokens=options.usage.prompt_tokens or estimate_tokens(prompt),
                completion_tokens=options.usage.completion_tokens,
            )
        trace.tokens = {
            "prompt": options.usage.prompt_tokens or estimate_tokens(prompt),
            "completion": options.usage.completion_tokens,
        }
        answer = "".join(parts).strip()

        verdict, reason = "skipped", "verification off"
        checked: list[claims.Claim] = []
        if config.agent.verification != "off":
            yield {"type": "status", "stage": "verify"}
            with trace.span("verify", method=config.agent.verification) as attributes:
                if config.agent.verification == "claims":
                    checked, verdict, reason = await self._check_claims(answer, result.selected, trace)
                    attributes["support"] = support_of(checked)
                elif config.agent.verification == "citations":
                    verdict, reason = verify_citations(answer, len(sources))
                else:
                    verdict, reason = await self._judge(llm, config, prompt, answer)
        trace.verify = verdict
        outcome = "answered"
        if verdict == "fail" and config.agent.on_verify_fail == "abstain":
            outcome, answer = "abstained", config.agent.abstain_message
            trace.decide(f"abstained: answer failed verification ({reason})")
        if buffered:
            yield {"type": "token", "text": answer}
        yield {"type": "verdict", "verdict": verdict, "reason": reason}
        trace.outcome = outcome

        used = sorted(set(cited_labels(answer)))
        citations = [sources[label - 1] for label in used if 1 <= label <= len(sources)] or (
            sources if outcome == "answered" else []
        )
        await self._remember(session_id, request.query, answer)
        if cacheable and outcome == "answered" and verdict != "fail":
            await asyncio.to_thread(self.cache.record, scope, question, vector, answer, citations, config.cache)
        yield {
            "type": "done",
            "outcome": outcome,
            "citations": citations,
            "verify": verdict,
            "verify_reason": reason,
            "claims": [
                {
                    "text": c.text,
                    "support": round(c.support, 3),
                    "backed": c.support >= claims.SUPPORTED,
                    "sources": [f"S{label}" for label in c.labels],
                }
                for c in checked
            ]
            if outcome == "answered"
            else [],
            "summary": answer_summary(trace),
            "trace_id": trace.id,
        }

    async def _retrieve_all(
        self,
        queries: list[str],
        vectors: dict[str, list[list[float] | None]],
        targets: list[Target],
        config: KnowledgeBaseConfig,
        reranker: Reranker | None,
        filters: dict[str, Any] | None,
        candidates: int,
    ) -> RetrievalResult:
        jobs = [(i, target) for i in range(len(queries)) for target in targets]
        results = await asyncio.gather(
            *(
                retrieve(
                    queries[i],
                    vectors[target.kb_id][i],
                    collection=target.collection,
                    config=target.config,
                    index=self.index,
                    reranker=reranker,
                    filters=filters,
                    candidates=candidates,
                )
                for i, target in jobs
            )
        )
        if config.retrieval.groups:  # name the group on each passage, for citations and the prompt
            for (_, target), result in zip(jobs, results, strict=True):
                for candidate in result.ranked:
                    candidate.payload = {**candidate.payload, "group": target.name}
        # Any group may hold the answer to a question, so the best group sets its confidence.
        by_query: list[list[RetrievalResult]] = [[] for _ in queries]
        for (i, _), result in zip(jobs, results, strict=True):
            by_query[i].append(result)
        per_query = [self._merge(found, config, config.rerank.top_n, max) for found in by_query]
        # Every sub-question needs evidence, so the weakest one sets the confidence.
        return self._merge(per_query, config, config.rerank.top_n * len(queries), min)

    @staticmethod
    def _merge(
        results: list[RetrievalResult], config: KnowledgeBaseConfig, top_n: int, confidence: Callable[..., float]
    ) -> RetrievalResult:
        if len(results) == 1:
            return results[0]

        def best_of(lists: list[list[Candidate]]) -> list[Candidate]:
            best: dict[str, Candidate] = {}
            for candidate in (c for found in lists for c in found):
                if candidate.id not in best or candidate.relevance > best[candidate.id].relevance:
                    best[candidate.id] = candidate
            return sorted(best.values(), key=lambda c: c.relevance, reverse=True)

        pool = best_of([r.selected for r in results])
        if config.context.dedup:
            pool = deduplicate(pool, config.context.near_duplicate_threshold)
        selected = fit_budget(pool, top_n, config.context.token_budget)
        keys = dict.fromkeys(key for r in results for key in r.funnel)
        funnel = {key: sum(r.funnel.get(key, 0) for r in results) for key in keys}
        funnel["context"] = len(selected)
        funnel["context_tokens"] = sum(estimate_tokens(c.text) for c in selected)
        ranked = best_of([r.ranked for r in results])  # the closest passages, shown when Archie abstains
        return RetrievalResult(selected, ranked, funnel, confidence(r.confidence for r in results))

    async def _check_claims(
        self, answer: str, passages: list[Candidate], trace: Trace
    ) -> tuple[list[claims.Claim], str, str]:
        """Each cited sentence against its passages; falls back to the citation check without the model."""
        labels = cited_labels(answer)
        if not labels or any(not 1 <= label <= len(passages) for label in labels):
            return [], *verify_citations(answer, len(passages))
        try:
            model = await asyncio.to_thread(self.providers.claim_checker)
            checked = await asyncio.to_thread(
                claims.check, model, claims.claims_of(answer, len(passages)), [p.text for p in passages]
            )
        except Exception as exc:
            trace.decide(f"claim check unavailable, checked citations instead ({type(exc).__name__})")
            logger.warning("claim check unavailable: %s", exc)
            return [], *verify_citations(answer, len(passages))
        verdict, reason = claims.verdict(checked)
        trace.decide(f"claim check: {reason}")
        return checked, verdict, reason

    async def _reranker(self, config: KnowledgeBaseConfig, trace: Trace) -> Reranker | None:
        if not config.rerank.enabled:
            return None
        try:
            return await asyncio.to_thread(self.providers.reranker, config.rerank.model)
        except Exception as exc:
            trace.decide(f"reranker unavailable, using retrieval scores ({type(exc).__name__})")
            logger.warning("reranker %s unavailable: %s", config.rerank.model, exc)
            return None

    @staticmethod
    def _chat_options(
        config: KnowledgeBaseConfig, *, max_tokens: int | None = None, temperature: float | None = None
    ) -> ChatOptions:
        generation = config.generation
        return ChatOptions(
            model=generation.model,
            temperature=generation.temperature if temperature is None else temperature,
            max_tokens=max_tokens or generation.max_tokens,
            context_window=generation.context_window,
            thinking=generation.thinking,
        )

    async def _complete(self, llm: ModelProvider, config: KnowledgeBaseConfig, prompt: str) -> str:
        """A short, deterministic helper call (condense, rewrite, decompose, judge)."""
        options = self._chat_options(config, max_tokens=200, temperature=0.0)
        options.thinking = False
        messages = [{"role": "user", "content": prompt}]
        return "".join([token async for token in llm.chat(messages, options)]).strip().strip('"')

    async def _judge(
        self, llm: ModelProvider, config: KnowledgeBaseConfig, prompt: str, answer: str
    ) -> tuple[str, str]:
        reply = await self._complete(llm, config, JUDGE_PROMPT.format(sources=prompt, answer=answer))
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        try:
            verdict = json.loads(match.group()).get("verdict", "") if match else ""
        except json.JSONDecodeError:
            verdict = ""
        if verdict == "unsupported":
            return "fail", "judge: unsupported"
        if verdict in ("supported", "partially_supported"):
            return "pass", f"judge: {verdict}"
        return "skipped", "judge reply was not valid JSON"

    async def _remember(self, session_id: str | None, question: str, answer: str) -> None:
        if session_id:
            await asyncio.to_thread(self.db.add_turn, session_id, question, answer, MEMORY_TURNS_KEPT)
