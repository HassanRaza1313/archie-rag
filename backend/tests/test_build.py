from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ragkit.config import KnowledgeBaseConfig, deep_merge, preset_config
from ragkit.index import VectorIndex
from ragkit.jobs import BUILD_STAGES, Builder
from ragkit.settings import Settings
from ragkit.storage import Database
from tests.conftest import FakeRegistry, kb_overrides


class World:
    def __init__(self, settings: Settings, registry: FakeRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self.db = Database(settings.db_path)
        self.index = VectorIndex(settings)
        self.builder = Builder(self.db, self.index, registry, settings, parse_workers=1)  # type: ignore[arg-type]

    def create(self, corpus: Path, **overrides: Any) -> str:
        config = KnowledgeBaseConfig.model_validate(
            deep_merge(preset_config("balanced").model_dump(), kb_overrides(corpus, **overrides))
        )
        return self.db.create_kb("sample", config)

    async def build(self, kb_id: str) -> dict[str, Any]:
        version, _ = self.db.get_config(kb_id)
        job_id = self.db.create_job(kb_id, version, BUILD_STAGES)
        job = self.db.claim_job()
        assert job and job["id"] == job_id
        await self.builder.run(job)
        return self.db.get_job(job_id) or {}


@pytest.fixture
def world(settings: Settings, registry: FakeRegistry) -> World:
    return World(settings, registry)


async def test_full_build_indexes_sample_corpus(world: World, corpus: Path) -> None:
    kb_id = world.create(corpus)
    job = await world.build(kb_id)
    assert job["status"] == "completed", job
    assert all(stage["status"] == "completed" for stage in job["stages"])
    summary = job["summary"]
    assert summary["documents"] == 3 and summary["total_chunks"] >= 6 and summary["tables"] == 2
    kb = world.db.get_kb(kb_id)
    assert kb["active_collection"] == summary["collection"]
    assert world.index.count(kb["active_collection"]) == summary["total_chunks"]
    payload = world.index.sample_payloads(kb["active_collection"], 1)[0]
    assert {"document_uri", "file_name", "section", "page_start", "text", "content_hash"} <= set(payload)


async def test_rebuild_is_incremental(world: World, corpus: Path) -> None:
    kb_id = world.create(corpus)
    await world.build(kb_id)
    embeds_after_first = world.registry.provider.embed_calls
    job = await world.build(kb_id)
    discover_stage = next(s for s in job["stages"] if s["stage"] == "discover")
    assert job["status"] == "completed"
    assert discover_stage["items_skipped"] == 3 and discover_stage["items_out"] == 0
    assert world.registry.provider.embed_calls - embeds_after_first <= 4  # probe + smoke-test queries only


async def test_changed_and_removed_files_are_reconciled(world: World, corpus: Path) -> None:
    kb_id = world.create(corpus)
    first = await world.build(kb_id)
    collection = first["summary"]["collection"]
    (corpus / "bicycle-maintenance-basics.md").unlink()
    handbook = corpus / "remote-team-handbook.md"
    handbook.write_text(
        handbook.read_text(encoding="utf-8") + "\n## Parking\n\nBikes go in the courtyard.\n", encoding="utf-8"
    )
    job = await world.build(kb_id)
    assert job["status"] == "completed"
    assert job["summary"]["removed"] == 1 and job["summary"]["skipped_unchanged"] == 1
    uris = set(world.db.indexed_documents(collection))
    assert uris == {"home-composting-guide.md", "remote-team-handbook.md"}
    texts = [p["text"] for p in world.index.sample_payloads(collection, 500)]
    assert any("courtyard" in t for t in texts)
    assert not any("chain checker" in t for t in texts)


async def test_corrupt_file_is_reported_not_fatal(world: World, corpus: Path) -> None:
    (corpus / "broken.pdf").write_bytes(b"%PDF-1.4 garbage")
    job = await world.build(world.create(corpus))
    assert job["status"] == "completed_with_errors"
    assert [f["document"] for f in job["failures"]] == ["broken.pdf"]
    assert job["summary"]["failed_documents"] == 1


async def test_empty_source_fails_with_reason(world: World, tmp_path: Path) -> None:
    empty = tmp_path / "data" / "empty"
    empty.mkdir()
    job = await world.build(world.create(empty))
    assert job["status"] == "failed" and "no supported files" in job["error"]
    assert next(s for s in job["stages"] if s["stage"] == "validate")["status"] == "failed"


async def test_unusable_embedding_model_fails_validation(world: World, corpus: Path) -> None:
    world.registry.provider.fail_embed = RuntimeError("model not found")
    job = await world.build(world.create(corpus))
    assert job["status"] == "failed" and "not usable" in job["error"]


async def test_cancel_stops_the_build(world: World, corpus: Path) -> None:
    kb_id = world.create(corpus)
    version, _ = world.db.get_config(kb_id)
    job_id = world.db.create_job(kb_id, version, BUILD_STAGES)
    job = world.db.claim_job()
    world.db.request_cancel(job_id)
    assert await world.builder.run(job) == "cancelled"
    assert world.db.get_kb(kb_id)["active_collection"] is None


async def test_index_change_builds_new_collection_and_drops_old(world: World, corpus: Path) -> None:
    kb_id = world.create(corpus)
    first = (await world.build(kb_id))["summary"]["collection"]
    _, config = world.db.get_config(kb_id)
    world.db.add_config_version(
        kb_id, config.model_copy(update={"chunking": config.chunking.model_copy(update={"chunk_size": 128})})
    )
    second_job = await world.build(kb_id)
    second = second_job["summary"]["collection"]
    assert second != first and world.db.get_kb(kb_id)["active_collection"] == second
    assert not world.index.exists(first) and world.db.indexed_documents(first) == {}


async def test_redact_at_ingest_removes_pii_from_index(world: World, corpus: Path) -> None:
    (corpus / "contacts.md").write_text(
        "# Contacts\n\nWrite to jane@example.com for compost questions.\n", encoding="utf-8"
    )
    kb_id = world.create(corpus, privacy={"redact_at_ingest": True})
    collection = (await world.build(kb_id))["summary"]["collection"]
    texts = " ".join(p["text"] for p in world.index.sample_payloads(collection, 500))
    assert "jane@example.com" not in texts and "[REDACTED_EMAIL_1]" in texts


async def test_process_pool_parsing(settings: Settings, registry: FakeRegistry, corpus: Path) -> None:
    world = World(settings, registry)
    world.builder.parse_workers = 2
    job = await world.build(world.create(corpus))
    assert job["status"] == "completed" and job["summary"]["documents"] == 3


async def test_broken_process_pool_falls_back_to_in_process(
    settings: Settings, registry: FakeRegistry, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import Future
    from concurrent.futures.process import BrokenProcessPool

    from tests.pdf_factory import PdfPage, write_pdf

    for name in ("a", "b"):
        write_pdf(corpus / f"{name}.pdf", [PdfPage().text(72, 700, 11, f"Document {name} talks about compost piles.")])

    class CrashingPool:
        def __init__(self, max_workers: int) -> None: ...
        def __enter__(self) -> CrashingPool:
            return self

        def __exit__(self, *_: object) -> None: ...
        def submit(self, *_: object, **__: object) -> Future[object]:
            future: Future[object] = Future()
            future.set_exception(BrokenProcessPool("worker died"))
            return future

    monkeypatch.setattr("ragkit.jobs.ProcessPoolExecutor", CrashingPool)
    world = World(settings, registry)
    world.builder.parse_workers = 2
    job = await world.build(world.create(corpus))
    assert job["status"] == "completed", job["failures"]
    assert job["summary"]["documents"] == 5


async def test_stage_status_when_every_document_fails(world: World, tmp_path: Path) -> None:
    broken = tmp_path / "data" / "broken"
    broken.mkdir()
    (broken / "x.pdf").write_bytes(b"not a pdf")
    job = await world.build(world.create(broken))
    statuses = {stage["stage"]: stage["status"] for stage in job["stages"]}
    assert job["status"] == "failed"
    assert statuses["parse"] == "failed"
    assert statuses["chunk"] == statuses["embed"] == statuses["index"] == "skipped"
