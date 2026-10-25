"""Build jobs and the worker loop.

A build turns a knowledge base's source into a searchable collection:

    validate -> provision -> discover -> parse -> chunk -> embed -> index -> reconcile -> smoke_test -> activate

Builds write into a collection named by the index fingerprint, so chat keeps using the previous collection
until ``activate`` swaps it atomically. Unchanged documents (same content hash) are skipped, which also makes
an interrupted build resumable: the next worker picks it up and continues where it stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import Any

from ragkit.chunking import Chunk, chunk_document
from ragkit.config import KnowledgeBaseConfig, ParsingConfig
from ragkit.index import VectorIndex, collection_name, point_id
from ragkit.parsing import ParsedDocument, parse_document
from ragkit.privacy import Redactor
from ragkit.providers import ProviderRegistry
from ragkit.settings import Settings
from ragkit.sources import DocumentRef, SourceError, discover
from ragkit.storage import TERMINAL_JOB_STATES, Database
from ragkit.telemetry import request_id_var
from ragkit.textutil import bm25_document_vector, bm25_query_vector

logger = logging.getLogger(__name__)

BUILD_STAGES = [
    "validate",
    "provision",
    "discover",
    "parse",
    "chunk",
    "embed",
    "index",
    "reconcile",
    "smoke_test",
    "activate",
]


class BuildCancelledError(Exception):
    pass


class BuildFailedError(Exception):
    pass


@dataclass
class _StageTracker:
    db: Database
    job_id: str
    counters: dict[str, dict[str, int]] = field(default_factory=dict)
    started: dict[str, float] = field(default_factory=dict)

    def start(self, stage: str, items_in: int = 0) -> None:
        self.started[stage] = time.time()
        self.counters[stage] = {"items_in": items_in, "items_out": 0, "items_skipped": 0, "items_failed": 0}
        self.db.update_stage(self.job_id, stage, status="running", started_at=self.started[stage], items_in=items_in)

    def add(self, stage: str, **increments: int) -> None:
        if stage not in self.counters:
            self.start(stage)
        for key, value in increments.items():
            self.counters[stage][key] += value
        self.db.update_stage(self.job_id, stage, **self.counters[stage])

    def finish(self, stage: str, status: str = "completed", detail: dict[str, Any] | None = None) -> None:
        if stage not in self.counters:
            self.start(stage)
        fields: dict[str, Any] = {"status": status, "finished_at": time.time(), **self.counters[stage]}
        if detail is not None:
            fields["detail"] = detail
        self.db.update_stage(self.job_id, stage, **fields)


def _parse_in_worker(path: str, parsing: dict[str, Any]) -> ParsedDocument:
    from pathlib import Path

    return parse_document(Path(path), ParsingConfig.model_validate(parsing))


class Builder:
    def __init__(
        self,
        db: Database,
        index: VectorIndex,
        providers: ProviderRegistry,
        settings: Settings,
        parse_workers: int | None = None,
    ) -> None:
        self.db = db
        self.index = index
        self.providers = providers
        self.settings = settings
        self.parse_workers = parse_workers if parse_workers is not None else min(4, os.cpu_count() or 1)

    async def run(self, job: dict[str, Any]) -> str:
        job_id, kb_id = job["id"], job["kb_id"]
        tracker = _StageTracker(self.db, job_id)
        _, config = self.db.get_config(kb_id, job["config_version"])
        summary: dict[str, Any] = {}
        try:
            status = await self._build(job_id, kb_id, job["config_version"], config, tracker, summary)
            self.db.finish_job(job_id, status, None, summary)
            return status
        except BuildCancelledError:
            self.db.finish_job(job_id, "cancelled", None, summary)
            return "cancelled"
        except (BuildFailedError, SourceError) as exc:
            self._fail_running_stage(tracker)
            self.db.finish_job(job_id, "failed", str(exc), summary)
            return "failed"
        except Exception as exc:
            logger.exception("build %s failed", job_id)
            self._fail_running_stage(tracker)
            self.db.finish_job(job_id, "failed", f"{type(exc).__name__}: {exc}", summary)
            return "failed"

    def _fail_running_stage(self, tracker: _StageTracker) -> None:
        job = self.db.get_job(tracker.job_id) or {"stages": []}
        for stage in job["stages"]:
            if stage["status"] == "running":
                tracker.finish(stage["stage"], status="failed")

    def _check(self, job_id: str) -> None:
        if self.db.heartbeat(job_id):
            raise BuildCancelledError

    async def _build(
        self,
        job_id: str,
        kb_id: str,
        version: int,
        config: KnowledgeBaseConfig,
        tracker: _StageTracker,
        summary: dict[str, Any],
    ) -> str:
        embedder = self.providers.get(config.embedding.provider)

        tracker.start("validate")
        refs = discover(config.source, self.settings, kb_id)
        if not refs:
            raise BuildFailedError("the source contains no supported files")
        try:
            probe = await embedder.embed(config.embedding.model, ["dimension probe"], "document")
        except Exception as exc:
            raise BuildFailedError(f"embedding model '{config.embedding.model}' is not usable: {exc}") from exc
        dimension = len(probe[0])
        tracker.finish("validate", detail={"files": len(refs), "embedding_dimension": dimension})

        tracker.start("provision")
        index_hash = config.index_fingerprint()
        collection = collection_name(kb_id, index_hash)
        created = not self.index.exists(collection)
        if created:
            self.db.forget_documents(collection)
            self.index.create(collection, dimension, config.index, config.retrieval.filter_fields)
        tracker.finish(
            "provision", detail={"collection": collection, "created": created, "qdrant_mode": self.index.mode}
        )

        tracker.start("discover", items_in=len(refs))
        known = self.db.indexed_documents(collection)
        hashes = {ref.uri: await asyncio.to_thread(ref.content_hash) for ref in refs}
        todo = [ref for ref in refs if known.get(ref.uri) != hashes[ref.uri]]
        removed = sorted(set(known) - set(hashes))
        tracker.add("discover", items_out=len(todo), items_skipped=len(refs) - len(todo))
        tracker.finish(
            "discover",
            detail={"new_or_changed": len(todo), "unchanged": len(refs) - len(todo), "removed": len(removed)},
        )
        self._check(job_id)

        profile = {"files": len(refs), "pages": 0, "ocr_pages": 0, "tables": 0, "chunks": 0}
        chunk_tokens: list[int] = []
        failures = 0
        for stage in ("parse", "chunk", "embed", "index"):
            tracker.start(stage, items_in=len(todo) if stage == "parse" else 0)
        warnings: set[str] = set()
        async for ref, parsed, error in self._parse_all(todo, config.parsing):
            self._check(job_id)
            if error is not None:
                failures += 1
                tracker.add("parse", items_failed=1)
                self.db.add_failure(job_id, ref.uri, "parse", error)
                continue
            assert parsed is not None
            tracker.add("parse", items_out=1)
            profile["pages"] += parsed.page_count
            profile["ocr_pages"] += parsed.ocr_pages
            profile["tables"] += parsed.table_count
            warnings.update(parsed.warnings)
            try:
                count = await self._index_document(
                    kb_id, collection, ref, hashes[ref.uri], parsed, config, tracker, chunk_tokens
                )
            except Exception as exc:
                failures += 1
                self.db.add_failure(job_id, ref.uri, "index", f"{type(exc).__name__}: {exc}")
                tracker.add("index", items_failed=1)
                continue
            profile["chunks"] += count
        for stage in ("parse", "chunk", "embed", "index"):
            counts = tracker.counters[stage]
            if stage == "parse" and counts["items_failed"] and not counts["items_out"]:
                status = "failed"
            elif stage != "parse" and todo and not counts["items_in"]:
                status = "skipped"  # nothing reached this stage
            else:
                status = "completed"
            tracker.finish(
                stage, status=status, detail={"warnings": sorted(warnings)} if stage == "parse" and warnings else None
            )

        tracker.start("reconcile", items_in=len(removed))
        self.index.delete_documents(collection, removed)
        self.db.forget_documents(collection, removed)
        tracker.add("reconcile", items_out=len(removed))
        tracker.finish("reconcile")
        self._check(job_id)

        tracker.start("smoke_test")
        stats = self.db.collection_stats(collection)
        if stats.get("chunks", 0) == 0:
            raise BuildFailedError("no chunks were indexed; check the failures and the parsing settings")
        hits = await self._smoke_test(collection, config)
        tracker.finish("smoke_test", detail={"queries": 3, "answered": hits})
        if hits == 0:
            raise BuildFailedError("smoke test: sample queries returned nothing")

        tracker.start("activate")
        previous = self.db.activate_index(kb_id, version, index_hash, collection)
        if previous and previous != collection:
            self.index.drop(previous)
            self.db.forget_documents(previous)
        tracker.finish("activate", detail={"collection": collection, "replaced": previous})

        chunk_tokens.sort()
        summary.update(
            profile,
            documents=stats["documents"],
            total_chunks=stats["chunks"],
            failed_documents=failures,
            chunk_tokens_p50=_percentile(chunk_tokens, 50),
            chunk_tokens_p95=_percentile(chunk_tokens, 95),
            collection=collection,
            skipped_unchanged=len(refs) - len(todo),
            removed=len(removed),
        )
        return "completed_with_errors" if failures else "completed"

    async def _parse_all(
        self, refs: list[DocumentRef], parsing: ParsingConfig
    ) -> AsyncIterator[tuple[DocumentRef, ParsedDocument | None, str | None]]:
        """Parse documents; yields (ref, document, error) as each finishes.

        PDFs are CPU-heavy and go to a process pool when there are several; other files parse in a thread. If the
        pool breaks (a worker crashed or could not start), the remaining documents are parsed in-process instead of
        failing the build.
        """
        heavy = [ref for ref in refs if ref.path.suffix.lower() == ".pdf"]
        workers = min(self.parse_workers, len(heavy))
        pooled = heavy if workers > 1 else []
        for ref in refs:
            if ref not in pooled:
                yield (ref, *await _parse_inline(ref, parsing))
        if not pooled:
            return
        loop = asyncio.get_running_loop()
        finished: set[str] = set()
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {
                    loop.run_in_executor(pool, _parse_in_worker, str(ref.path), parsing.model_dump()): ref
                    for ref in pooled
                }
                pending = set(futures)
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for future in done:
                        ref = futures[future]
                        try:
                            document = future.result()
                        except BrokenProcessPool:
                            for other in futures:  # mark sibling failures as handled so asyncio does not log them
                                if other.done() and not other.cancelled():
                                    other.exception()
                                else:
                                    other.cancel()
                            raise
                        except Exception as exc:
                            finished.add(ref.uri)
                            yield ref, None, f"{type(exc).__name__}: {exc}"
                        else:
                            finished.add(ref.uri)
                            yield ref, document, None
        except BrokenProcessPool:
            logger.warning("parser process pool broke; parsing the remaining documents in-process")
            for ref in pooled:
                if ref.uri not in finished:
                    yield (ref, *await _parse_inline(ref, parsing))

    async def _index_document(
        self,
        kb_id: str,
        collection: str,
        ref: DocumentRef,
        content_hash: str,
        parsed: ParsedDocument,
        config: KnowledgeBaseConfig,
        tracker: _StageTracker,
        chunk_tokens: list[int],
    ) -> int:
        chunks = chunk_document(parsed, config.chunking)
        if config.privacy.redact_at_ingest:
            redactor = Redactor(config.privacy.detectors)
            for chunk in chunks:
                chunk.text, chunk.embed_text = redactor.redact(chunk.text), redactor.redact(chunk.embed_text)
        tracker.add("chunk", items_in=1, items_out=len(chunks))
        chunk_tokens.extend(chunk.tokens for chunk in chunks)
        embedder = self.providers.get(config.embedding.provider)
        size = config.embedding.batch_size
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), size):
            batch = chunks[start : start + size]
            vectors.extend(await embedder.embed(config.embedding.model, [c.embed_text for c in batch], "document"))
            tracker.add("embed", items_in=len(batch), items_out=len(batch))
        self.index.delete_documents(collection, [ref.uri])
        points = [
            self._point(kb_id, ref, content_hash, parsed, chunk, vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        for start in range(0, len(points), 256):
            await asyncio.to_thread(self.index.upsert, collection, points[start : start + 256])
        tracker.add("index", items_in=len(points), items_out=len(points))
        self.db.record_document(collection, ref.uri, content_hash, len(chunks))
        return len(chunks)

    @staticmethod
    def _point(
        kb_id: str, ref: DocumentRef, content_hash: str, parsed: ParsedDocument, chunk: Chunk, vector: list[float]
    ) -> tuple[str, list[float], dict[int, float], dict[str, Any]]:
        payload = {
            "kb_id": kb_id,
            "document_uri": ref.uri,
            "file_name": ref.path.name,
            "file_type": ref.path.suffix.lower().lstrip("."),
            "title": parsed.title,
            "section": chunk.section,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "content_type": chunk.content_type,
            "ordinal": chunk.ordinal,
            "token_count": chunk.tokens,
            "content_hash": content_hash,
            "ingested_at": time.time(),
            "text": chunk.text,
        }
        return point_id(kb_id, content_hash, chunk.ordinal), vector, bm25_document_vector(chunk.embed_text), payload

    async def _smoke_test(self, collection: str, config: KnowledgeBaseConfig) -> int:
        """Search for words taken from three indexed chunks; each should find something."""
        samples = await asyncio.to_thread(self.index.sample_payloads, collection, 3)
        embedder = self.providers.get(config.embedding.provider)
        found = 0
        for payload in samples:
            query = " ".join(str(payload.get("section") or payload.get("text", "")).split()[:12])
            vector = (await embedder.embed(config.embedding.model, [query], "query"))[0]
            dense, keyword = await asyncio.to_thread(
                self.index.search, collection, vector, bm25_query_vector(query), 3, None, config.retrieval.search_ef
            )
            found += bool(dense or keyword)
        return found


def _percentile(values: list[int], pct: int) -> int:
    if not values:
        return 0
    return values[min(len(values) - 1, round((len(values) - 1) * pct / 100))]


async def run_worker(builder: Builder, poll_s: float = 1.0, stop: asyncio.Event | None = None) -> None:
    """Claim and run queued builds until ``stop`` is set; periodically purge expired traces."""
    stop = stop or asyncio.Event()
    last_purge = 0.0
    while not stop.is_set():
        job = await asyncio.to_thread(builder.db.claim_job)
        if job is not None:
            token = request_id_var.set(job["id"])
            try:
                logger.info("build started for %s", job["kb_id"])
                status = await builder.run(job)
                logger.info("build finished: %s", status)
            finally:
                request_id_var.reset(token)
            continue
        if time.time() - last_purge > 3600:
            purge_traces(builder.db)
            last_purge = time.time()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll_s)


def purge_traces(db: Database) -> None:
    for kb in db.list_kbs():
        _, config = db.get_config(kb["id"])
        db.purge_traces(kb["id"], time.time() - config.telemetry.retention_days * 86400)


def is_terminal(status: str) -> bool:
    return status in TERMINAL_JOB_STATES


async def _parse_inline(ref: DocumentRef, parsing: ParsingConfig) -> tuple[ParsedDocument | None, str | None]:
    try:
        return await asyncio.to_thread(parse_document, ref.path, parsing), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
