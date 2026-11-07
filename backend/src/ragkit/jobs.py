"""Build jobs and the worker loop.

A build turns a knowledge base's source into a searchable collection:

    validate -> provision -> discover -> parse -> chunk -> enrich -> embed -> index -> reconcile -> smoke_test
    -> activate

Builds write into a collection named by the index fingerprint, so chat keeps using the previous collection
until ``activate`` swaps it atomically. Unchanged documents (same content hash) are skipped, which also makes
an interrupted build resumable: the next worker picks it up and continues where it stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import sys
import time
from collections import Counter, deque
from collections.abc import AsyncIterator
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import Any

from ragkit.chunking import Chunk, chunk_document
from ragkit.config import KnowledgeBaseConfig, ParsingConfig
from ragkit.enrichment import Enrichment, enrich_chunks
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
    "enrich",
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


def _limit_memory(megabytes: int) -> None:
    """Process-pool initializer: cap the parser's address space so a runaway file cannot exhaust the host."""
    if megabytes and sys.platform != "win32":
        import resource

        limit = megabytes * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _terminate(pool: Any) -> None:
    # shortcut: ProcessPoolExecutor has no public way to stop a running task; kill its workers directly.
    for process in list(getattr(pool, "_processes", {}).values()):
        process.terminate()
    with contextlib.suppress(Exception):
        pool.shutdown(wait=False, cancel_futures=True)


def _retrieve(future: asyncio.Future[Any]) -> None:
    """Mark a finished future's exception as seen, so abandoned futures do not log "never retrieved"."""
    if not future.cancelled():
        future.exception()


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

        profile = {"files": len(refs), "pages": 0, "ocr_pages": 0, "tables": 0, "chunks": 0, "duplicates": 0}
        chunk_tokens: list[int] = []
        failures = 0
        seen: dict[str, str] = {}  # normalised chunk text hash -> document that first had it
        enrichment_counts: Counter[str] = Counter()
        for stage in PIPELINE_STAGES:
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
                    kb_id,
                    collection,
                    ref,
                    hashes[ref.uri],
                    parsed,
                    config,
                    tracker,
                    chunk_tokens,
                    seen,
                    enrichment_counts,
                )
            except Exception as exc:
                failures += 1
                self.db.add_failure(job_id, ref.uri, "index", f"{type(exc).__name__}: {exc}")
                tracker.add("index", items_failed=1)
                continue
            profile["chunks"] += count
        profile["duplicates"] = enrichment_counts.pop("duplicates", 0)
        for stage in PIPELINE_STAGES:
            counts = tracker.counters[stage]
            if stage == "parse" and counts["items_failed"] and not counts["items_out"]:
                status = "failed"
            elif stage == "enrich" and not config.enrichment.enabled:
                status = "skipped"
            elif stage != "parse" and todo and not counts["items_in"]:
                status = "skipped"  # nothing reached this stage
            else:
                status = "completed"
            detail: dict[str, Any] | None = None
            if stage == "parse" and warnings:
                detail = {"warnings": sorted(warnings)}
            elif stage == "enrich" and config.enrichment.enabled:
                detail = dict(enrichment_counts)
            tracker.finish(stage, status=status, detail=detail)

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

        Plain text formats parse in a thread. Everything else parses in worker processes, each file under a time
        limit (and, on Linux and macOS, a memory limit), so a file that hangs, crashes its parser or exhausts
        memory fails on its own and the build carries on:

        - a file over the time limit fails; the pool is restarted for the files still waiting;
        - when a worker crashes, the files that were running are retried one at a time, so only the file that
          crashes on its own fails;
        - if worker processes cannot run at all on this host (no file has ever finished in one), the rest are
          parsed in-process instead.
        """
        light = [ref for ref in refs if ref.path.suffix.lower() in _INLINE_SUFFIXES]
        for ref in light:
            yield (ref, *await _parse_inline(ref, parsing))
        queue = deque(ref for ref in refs if ref not in light)
        if self.parse_workers <= 0:
            while queue:
                ref = queue.popleft()
                yield (ref, *await _parse_inline(ref, parsing))
            return
        loop = asyncio.get_running_loop()
        options = parsing.model_dump()
        timeout = self.settings.parse_timeout_s
        suspects: set[str] = set()  # running when a worker crashed: retried alone
        pools_worked = False
        while queue:
            alone = queue[0].uri in suspects
            if alone and not pools_worked:
                logger.warning("parser processes are not usable here; parsing the remaining documents in-process")
                while queue:
                    ref = queue.popleft()
                    yield (ref, *await _parse_inline(ref, parsing))
                return
            workers = 1 if alone else min(self.parse_workers, len(queue))
            pool = ProcessPoolExecutor(
                max_workers=workers, initializer=_limit_memory, initargs=(self.settings.parse_memory_mb,)
            )
            running: dict[asyncio.Future[Any], tuple[DocumentRef, float]] = {}
            try:
                while queue or running:
                    while queue and len(running) < workers and (queue[0].uri in suspects) == alone:
                        ref = queue.popleft()
                        future = loop.run_in_executor(pool, _parse_in_worker, str(ref.path), options)
                        future.add_done_callback(_retrieve)
                        running[future] = (ref, time.monotonic())
                    if not running:
                        break  # the next file needs a pool of a different kind
                    oldest = min(started for _, started in running.values())
                    done, _ = await asyncio.wait(
                        running, timeout=max(0.0, oldest + timeout - time.monotonic()), return_when=FIRST_COMPLETED
                    )
                    if not done:
                        late = min(running, key=lambda future: running[future][1])
                        ref, _ = running.pop(late)
                        yield ref, None, f"parsing took longer than {timeout:.0f} s, so this file was skipped"
                        queue.extendleft(reversed([other for other, _ in running.values()]))
                        running.clear()
                        _terminate(pool)
                        break
                    crashed = False
                    for future in done:
                        ref, _ = running.pop(future)
                        try:
                            document = future.result()
                        except BrokenProcessPool:
                            crashed = True
                            if alone:
                                yield ref, None, "the parser crashed on this file (it may have run out of memory)"
                            else:
                                suspects.add(ref.uri)
                                queue.appendleft(ref)
                        except Exception as exc:
                            pools_worked = True
                            yield ref, None, f"{type(exc).__name__}: {exc}"
                        else:
                            pools_worked = True
                            yield ref, document, None
                    if crashed:
                        for ref, _ in running.values():
                            suspects.add(ref.uri)
                            queue.appendleft(ref)
                        running.clear()
                        _terminate(pool)
                        break
            finally:
                for future in running:
                    future.cancel()
                with contextlib.suppress(Exception):
                    pool.shutdown(wait=False, cancel_futures=True)

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
        seen: dict[str, str],
        enrichment_counts: Counter[str],
    ) -> int:
        chunks = chunk_document(parsed, config.chunking)
        produced = len(chunks)
        edits = self.db.chunk_edits(kb_id, ref.uri, content_hash)
        kept: list[Chunk] = []
        duplicates = 0
        for chunk in chunks:
            edit = edits.get(chunk.ordinal)
            if edit and edit["text"]:
                chunk.embed_text = chunk.embed_text.replace(chunk.text, edit["text"])
                chunk.text = edit["text"]
            # shortcut: duplicates are found within one build; an unchanged file indexed earlier is not compared.
            digest = hashlib.sha1(" ".join(chunk.text.lower().split()).encode(), usedforsecurity=False).hexdigest()
            if seen.setdefault(digest, ref.uri) != ref.uri and chunk.tokens >= 8:
                duplicates += 1
                continue
            kept.append(chunk)
        chunks = kept
        enrichment_counts["duplicates"] += duplicates
        if config.privacy.redact_at_ingest:
            redactor = Redactor(config.privacy.detectors)
            for chunk in chunks:
                chunk.text, chunk.embed_text = redactor.redact(chunk.text), redactor.redact(chunk.embed_text)
        tracker.add("chunk", items_in=1, items_out=len(chunks))
        chunk_tokens.extend(chunk.tokens for chunk in chunks)
        enrichments = [Enrichment() for _ in chunks]
        if config.enrichment.enabled and chunks:
            provider = self.providers.get(config.enrichment.provider)
            enrichments, counts = await enrich_chunks(chunks, parsed, config.enrichment, provider, self.db)
            enrichment_counts.update(counts)
            for chunk, enrichment in zip(chunks, enrichments, strict=True):
                if extra := enrichment.search_text():
                    chunk.embed_text = f"{extra}\n\n{chunk.embed_text}"
            tracker.add("enrich", items_in=len(chunks), items_out=len(chunks) - counts["failed"])
        embedder = self.providers.get(config.embedding.provider)
        size = config.embedding.batch_size
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), size):
            batch = chunks[start : start + size]
            vectors.extend(await embedder.embed(config.embedding.model, [c.embed_text for c in batch], "document"))
            tracker.add("embed", items_in=len(batch), items_out=len(batch))
        self.index.delete_documents(collection, [ref.uri])
        points = [
            self._point(kb_id, ref, content_hash, parsed, chunk, vector, enrichment)
            for chunk, vector, enrichment in zip(chunks, vectors, enrichments, strict=True)
        ]
        for start in range(0, len(points), 256):
            await asyncio.to_thread(self.index.upsert, collection, points[start : start + 256])
        tracker.add("index", items_in=len(points), items_out=len(points))
        templates = Counter(chunk.template for chunk in chunks)
        for chunk in chunks:
            if (edit := edits.get(chunk.ordinal)) and edit["excluded"]:
                chunk.excluded = True
        report = {
            "reader": parsed.reader,
            "template": templates.most_common(1)[0][0] if templates else None,
            "pages": parsed.page_count,
            "ocr_pages": parsed.ocr_pages,
            "tables": parsed.table_count,
            "layout_hints": parsed.layout_hints,
            "warnings": parsed.warnings,
            "chunks_produced": produced,
            "duplicates": duplicates,
            "edited": len(edits),
            "quality": parse_quality(parsed, chunks),
        }
        self.db.record_document(collection, ref.uri, content_hash, len(chunks), report)
        return len(chunks)

    @staticmethod
    def _point(
        kb_id: str,
        ref: DocumentRef,
        content_hash: str,
        parsed: ParsedDocument,
        chunk: Chunk,
        vector: list[float],
        enrichment: Enrichment,
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
            "template": chunk.template,
            "token_count": chunk.tokens,
            "content_hash": content_hash,
            "ingested_at": time.time(),
            "text": chunk.text,
        }
        if chunk.excluded:
            payload["excluded"] = True
        if enrichment.search_text():
            payload["enrichment"] = {
                "context": enrichment.context,
                "questions": enrichment.questions,
                "keywords": enrichment.keywords,
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


PIPELINE_STAGES = ("parse", "chunk", "enrich", "embed", "index")
_INLINE_SUFFIXES = {".md", ".txt", ".html"}  # cheap, pure-Python parsers that cannot hang or crash the process
_READABLE = set("abcdefghijklmnopqrstuvwxyz0123456789 .,;:!?'\"()-%$€£/&|›*#+=\n")


def parse_quality(parsed: ParsedDocument, chunks: list[Chunk]) -> float:
    """A 0-1 estimate of how well a document was read, to point people at the files worth checking.

    Penalises complex layouts read by the fast reader, OCR'd pages, garbled text, fragment-sized chunks and
    parser warnings.
    """
    if not chunks:
        return 0.0
    score = 1.0
    if parsed.layout_hints and parsed.reader == "fast":
        score -= 0.35
    if parsed.page_count and parsed.ocr_pages:
        score -= 0.2 * parsed.ocr_pages / parsed.page_count
    text = " ".join(chunk.text for chunk in chunks).lower()
    readable = sum(char in _READABLE for char in text) / max(1, len(text))
    score -= max(0.0, 0.9 - readable) * 2
    score -= 0.2 * sum(chunk.tokens < 12 for chunk in chunks) / len(chunks)
    score -= min(0.2, 0.1 * len(parsed.warnings))
    return round(max(0.0, min(1.0, score)), 2)


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
