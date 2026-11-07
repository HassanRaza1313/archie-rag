"""Contextual enrichment: a chat model describes each chunk at build time so search can find it.

For every chunk the model sees the document (or, for long documents, its opening and the text around the
chunk) and writes, as requested: a sentence or two that situates the chunk in the document (contextual
retrieval), questions the chunk answers, and search keywords including synonyms. These are added to what is
embedded and keyword-indexed, never to the text shown or sent to the answering model.

Results are cached by content and model, so rebuilding unchanged documents costs no model calls. A failed or
malformed reply leaves that chunk unenriched instead of failing the build.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ragkit.chunking import Chunk
from ragkit.config import EnrichmentConfig
from ragkit.parsing import ParsedDocument
from ragkit.providers import ChatOptions, ModelProvider
from ragkit.storage import Database
from ragkit.textutil import estimate_tokens

logger = logging.getLogger(__name__)

PROMPT_VERSION = 1
_ASKS = {
    "context": '"context": one or two sentences that situate the passage within the document (what it is about, '
    "which part it belongs to), to improve search. Do not repeat the passage.",
    "questions": '"questions": up to 3 short questions this passage answers.',
    "keywords": '"keywords": up to 6 search keywords, including synonyms that do not appear in the passage.',
}
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class Enrichment:
    context: str = ""
    questions: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    def search_text(self) -> str:
        parts = [self.context]
        if self.questions:
            parts.append("Questions: " + " ".join(self.questions))
        if self.keywords:
            parts.append("Keywords: " + ", ".join(self.keywords))
        return "\n".join(part for part in parts if part)


def requested(config: EnrichmentConfig) -> list[str]:
    return [name for name in _ASKS if getattr(config, name)]


def excerpt(document: ParsedDocument, chunk: Chunk, budget_tokens: int) -> str:
    """The whole document if it fits the budget, else its opening plus the text around the chunk."""
    full = "\n\n".join(block.text for block in document.blocks)
    if estimate_tokens(full) <= budget_tokens:
        return full
    half = budget_tokens * 2  # characters per half budget at ~4 characters per token
    at = max(0, full.find(chunk.text[:80]))
    return full[:half] + "\n\n[…]\n\n" + full[max(half, at - half // 2) : at + len(chunk.text) + half // 2]


def build_prompt(document: ParsedDocument, chunk: Chunk, config: EnrichmentConfig) -> str:
    asks = "\n".join(f"- {_ASKS[name]}" for name in requested(config))
    keys = ", ".join(f'"{name}"' for name in requested(config))
    return (
        f'<document title="{document.title}">\n{excerpt(document, chunk, config.document_tokens)}\n</document>\n\n'
        f"Here is a passage from this document (section: {chunk.section or 'none'}):\n"
        f"<passage>\n{chunk.text}\n</passage>\n\n"
        f"Reply with one JSON object with the keys {keys}, and nothing else.\n{asks}"
    )


def parse_reply(reply: str) -> Enrichment:
    match = _JSON_OBJECT.search(reply)
    if not match:
        raise ValueError("no JSON object in the reply")
    data = json.loads(match.group(0))

    def strings(value: Any, limit: int) -> list[str]:
        items = value if isinstance(value, list) else []
        return [" ".join(str(item).split()) for item in items if str(item).strip()][:limit]

    return Enrichment(
        context=" ".join(str(data.get("context") or "").split())[:600],
        questions=strings(data.get("questions"), 3),
        keywords=strings(data.get("keywords"), 6),
    )


def cache_key(chunk: Chunk, document: ParsedDocument, config: EnrichmentConfig) -> str:
    payload = [PROMPT_VERSION, config.provider, config.model, requested(config), config.document_tokens]
    payload += [document.title, chunk.section, chunk.text]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


async def enrich_chunks(
    chunks: list[Chunk],
    document: ParsedDocument,
    config: EnrichmentConfig,
    provider: ModelProvider,
    db: Database,
) -> tuple[list[Enrichment], dict[str, int]]:
    """Enrich every chunk; returns one Enrichment per chunk and counts of cached, generated and failed calls."""
    counts = {"cached": 0, "generated": 0, "failed": 0}
    if not requested(config):
        return [Enrichment() for _ in chunks], counts
    gate = asyncio.Semaphore(config.concurrency)

    async def one(chunk: Chunk) -> Enrichment:
        key = cache_key(chunk, document, config)
        if (hit := db.enrichment_get(key)) is not None:
            counts["cached"] += 1
            return Enrichment(**hit)
        async with gate:
            try:
                options = ChatOptions(model=config.model, temperature=0.0, max_tokens=300)
                messages = [{"role": "user", "content": build_prompt(document, chunk, config)}]
                reply = "".join([token async for token in provider.chat(messages, options)])
                result = parse_reply(reply)
            except Exception as exc:
                counts["failed"] += 1
                logger.warning("enrichment failed for a chunk of %s: %s", document.title, exc)
                return Enrichment()
        counts["generated"] += 1
        db.enrichment_put(key, {"context": result.context, "questions": result.questions, "keywords": result.keywords})
        return result

    return list(await asyncio.gather(*(one(chunk) for chunk in chunks))), counts
