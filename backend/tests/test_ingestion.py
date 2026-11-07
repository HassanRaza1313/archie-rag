"""Ingestion upgrades: readers and routing, chunking templates, enrichment, small-to-big, isolation, edits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ragkit.api import create_app
from ragkit.chunking import Chunk, choose_template, chunk_document
from ragkit.config import ChunkingConfig, EnrichmentConfig, ParsingConfig
from ragkit.enrichment import Enrichment, build_prompt, enrich_chunks, excerpt, parse_reply
from ragkit.jobs import parse_quality
from ragkit.parsing import Block, ParsedDocument, parse_document
from ragkit.retrieval import _join
from ragkit.settings import Settings
from ragkit.storage import Database
from tests.conftest import FakeProvider, FakeRegistry
from tests.pdf_factory import PdfPage, write_pdf
from tests.test_api import build, create_kb
from tests.test_build import World

# Readers and routing


def test_html_reader_keeps_structure_and_skips_chrome(tmp_path: Path) -> None:
    page = tmp_path / "faq.html"
    page.write_text(
        "<html><head><title>FAQ</title><style>p{}</style></head><body><nav>Home | Docs</nav>"
        "<h1>FAQ</h1><h2>How do I export?</h2><p>Open <b>Settings</b>, then Export.</p>"
        "<table><tr><th>Plan</th><th>Price</th></tr><tr><td>Team</td><td>$49</td></tr></table>"
        "<script>alert(1)</script></body></html>",
        encoding="utf-8",
    )
    document = parse_document(page, ParsingConfig())
    assert document.title == "FAQ" and document.file_type == ".html"
    assert [(b.kind, b.text.splitlines()[0]) for b in document.blocks] == [
        ("heading", "FAQ"),
        ("heading", "How do I export?"),
        ("text", "Open Settings, then Export."),
        ("table", "| Plan | Price |"),
    ]
    assert not any("Home" in b.text or "alert" in b.text for b in document.blocks)


def _borderless_table(path: Path) -> Path:
    page = PdfPage().text(72, 740, 16, "Pricing", bold=True)
    for row, cells in enumerate([("Plan", "Price", "Seats"), ("Starter", "$19", "3"), ("Team", "$49", "10")]):
        for x, cell in zip((72, 260, 420), cells, strict=True):
            page.text(x, 700 - row * 20, 10, cell)
    return write_pdf(path, [page])


def test_fast_reader_flags_complex_layouts(tmp_path: Path) -> None:
    document = parse_document(_borderless_table(tmp_path / "p.pdf"), ParsingConfig(engine="fast"))
    assert document.reader == "fast"
    assert document.layout_hints == ["1 page(s) with columns or borderless tables"]


def test_auto_reader_keeps_fast_result_when_layout_extra_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ragkit.layout.layout_available", lambda: False)
    document = parse_document(_borderless_table(tmp_path / "p.pdf"), ParsingConfig(engine="auto"))
    assert document.reader == "fast"
    assert any("layout" in warning for warning in document.warnings)


def test_auto_reader_upgrades_complex_pdfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []

    def fake_layout(path: Path, config: ParsingConfig) -> ParsedDocument:
        calls.append(path)
        return ParsedDocument("Pricing", [Block("table", "| Plan | Price |", 1, 1)], reader="layout")

    monkeypatch.setattr("ragkit.layout.layout_available", lambda: True)
    monkeypatch.setattr("ragkit.layout.parse_layout", fake_layout)
    pdf = _borderless_table(tmp_path / "p.pdf")
    document = parse_document(pdf, ParsingConfig(engine="auto"))
    assert calls == [pdf] and document.reader == "layout" and document.layout_hints
    simple = write_pdf(tmp_path / "s.pdf", [PdfPage().text(72, 700, 11, "One plain paragraph of text.")])
    assert parse_document(simple, ParsingConfig(engine="auto")).reader == "fast"
    assert calls == [pdf]


def test_auto_reader_falls_back_when_layout_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: Path, config: ParsingConfig) -> ParsedDocument:
        raise RuntimeError("model crashed")

    monkeypatch.setattr("ragkit.layout.layout_available", lambda: True)
    monkeypatch.setattr("ragkit.layout.parse_layout", broken)
    document = parse_document(_borderless_table(tmp_path / "p.pdf"), ParsingConfig(engine="auto"))
    assert document.reader == "fast" and any("layout reader failed" in w for w in document.warnings)


def test_layout_reader_requires_its_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ragkit.layout.layout_available", lambda: False)
    with pytest.raises(ValueError, match="'layout' extra"):
        parse_document(tmp_path / "x.docx", ParsingConfig(engine="auto"))


# Chunking templates


def _doc(blocks: list[Block], file_type: str = ".pdf") -> ParsedDocument:
    return ParsedDocument("Doc", blocks, file_type=file_type)


def test_auto_template_choice() -> None:
    faq = [Block("heading", "FAQ", 1, 1, 1)] + [Block("heading", f"How do I do {n}?", 1, 1, 2) for n in range(3)]
    clauses = [Block("heading", f"{n}. Clause", 1, 1, 2) for n in range(1, 4)]
    paper = [Block("heading", name, 1, 1, 2) for name in ("Abstract", "Method", "References")]
    assert choose_template(_doc([], ".xlsx"))[0] == "rows"
    assert choose_template(_doc([], ".pptx"))[0] == "slides"
    assert choose_template(_doc(faq))[0] == "sections"
    assert choose_template(_doc(clauses))[0] == "sections"
    assert choose_template(_doc(paper))[0] == "paper"
    assert choose_template(_doc([Block("heading", "Introduction", 1, 1, 2)]))[0] == "structure"


def test_sections_template_keeps_small_sections_apart() -> None:
    blocks = [Block("heading", "Q1?", 1, 1, 2), Block("text", "Answer one.", 1, 1)]
    blocks += [Block("heading", "Q2?", 1, 1, 2), Block("text", "Answer two.", 1, 1)]
    merged = chunk_document(_doc(blocks), ChunkingConfig(strategy="structure"))
    separate = chunk_document(_doc(blocks), ChunkingConfig(strategy="sections"))
    assert len(merged) == 1 and [c.text for c in separate] == ["Answer one.", "Answer two."]
    assert {c.template for c in separate} == {"sections"}


def test_slides_template_splits_per_page() -> None:
    blocks = [Block("heading", "Intro", 1, 1, 1), Block("text", "Hello.", 1, 1)]
    blocks += [Block("text", "Second slide.", 2, 2)]
    chunks = chunk_document(_doc(blocks, ".pptx"), ChunkingConfig(strategy="auto"))
    assert [(c.text, c.page_start, c.template) for c in chunks] == [
        ("Hello.", 1, "slides"),
        ("Second slide.", 2, "slides"),
    ]


def test_rows_template_makes_small_row_groups_with_header() -> None:
    rows = "\n".join(f"| item {n} | warehouse {n} | {n * 7} |" for n in range(60))
    table = Block("table", "| Item | Warehouse | Qty |\n|---|---|---|\n" + rows, 1, 1)
    whole = chunk_document(_doc([table]), ChunkingConfig(strategy="structure"))
    small = chunk_document(_doc([table], ".xlsx"), ChunkingConfig(strategy="auto"))
    assert len(small) > len(whole)
    assert all(c.text.startswith("| Item | Warehouse | Qty |") for c in small)


def test_paper_template_drops_references() -> None:
    blocks = [Block("heading", "Abstract", 1, 1, 2), Block("text", "Yield reached 92 percent.", 1, 1)]
    blocks += [Block("heading", "References", 2, 2, 2), Block("text", "[1] Smith 2019, yield 70 percent.", 2, 2)]
    chunks = chunk_document(_doc(blocks), ChunkingConfig(strategy="auto"))
    assert [c.text for c in chunks] == ["Yield reached 92 percent."]


# Enrichment


def _chunk(text: str = "Backups are kept for 35 days.") -> Chunk:
    return Chunk(0, text, text, "Backups", 1, 1, "text", 8)


def test_enrichment_reply_parsing() -> None:
    reply = 'Sure: {"context": " From the  retention rules. ", "questions": ["How long?", ""], "keywords": "x"}'
    assert parse_reply(reply) == Enrichment("From the retention rules.", ["How long?"], [])
    with pytest.raises(ValueError):
        parse_reply("no json here")
    assert Enrichment("C", ["Q?"], ["k1", "k2"]).search_text() == "C\nQuestions: Q?\nKeywords: k1, k2"


def test_enrichment_excerpt_and_prompt() -> None:
    chunk = _chunk()
    small = ParsedDocument("Rules", [Block("text", chunk.text, 1, 1)])
    assert excerpt(small, chunk, 1000) == chunk.text
    long = ParsedDocument("Rules", [Block("text", "filler " * 4000, 1, 1), Block("text", chunk.text, 1, 1)])
    cut = excerpt(long, chunk, 200)
    assert len(cut) < 3000 and chunk.text in cut
    prompt = build_prompt(small, chunk, EnrichmentConfig(context=True, keywords=True))
    assert '"context", "keywords"' in prompt and '"questions"' not in prompt and "<passage>" in prompt


async def test_enrichment_uses_the_cache(tmp_path: Path) -> None:
    provider = FakeProvider()
    provider.script = lambda _: (
        '{"context": "From the data retention rules.", "questions": ["How long are backups kept?"]}'
    )
    db = Database(tmp_path / "db.sqlite")
    config = EnrichmentConfig(context=True, questions=True)
    document = ParsedDocument("Rules", [Block("text", "x", 1, 1)])
    first, counts = await enrich_chunks([_chunk()], document, config, provider, db)  # type: ignore[arg-type]
    assert counts == {"cached": 0, "generated": 1, "failed": 0}
    assert first[0].context == "From the data retention rules."
    again, counts = await enrich_chunks([_chunk()], document, config, provider, db)  # type: ignore[arg-type]
    assert counts["cached"] == 1 and len(provider.chat_calls) == 1 and again == first


async def test_enrichment_failure_leaves_chunk_unenriched(tmp_path: Path) -> None:
    provider = FakeProvider()
    provider.script = lambda _: "not json"
    db = Database(tmp_path / "db.sqlite")
    document = ParsedDocument("Rules", [Block("text", "x", 1, 1)])
    result, counts = await enrich_chunks([_chunk()], document, EnrichmentConfig(context=True), provider, db)  # type: ignore[arg-type]
    assert result == [Enrichment()] and counts["failed"] == 1


async def test_build_with_enrichment_indexes_it(settings: Settings, registry: FakeRegistry, corpus: Path) -> None:
    def script(messages: list[dict[str, str]]) -> str:
        return '{"context": "Part of a home guide.", "keywords": ["mulch"]}'

    registry.provider.script = script
    world = World(settings, registry)
    job = await world.build(world.create(corpus, enrichment={"context": True, "keywords": True}))
    assert job["status"] == "completed", job
    stage = next(s for s in job["stages"] if s["stage"] == "enrich")
    assert stage["status"] == "completed" and stage["detail"]["generated"] > 0
    payload = world.index.sample_payloads(job["summary"]["collection"], 1)[0]
    assert payload["enrichment"] == {"context": "Part of a home guide.", "questions": [], "keywords": ["mulch"]}


# Small-to-big, quality, duplicates, isolation


def test_join_drops_repeated_overlap() -> None:
    assert _join("", "First. Second.") == "First. Second."
    assert _join("One. Two. Three.", "Three. Four.") == "One. Two. Three. Four."
    assert _join("One.", "Two.") == "One. Two."


def test_parse_quality_flags_doubtful_reads() -> None:
    text = "A clean readable paragraph about backups and retention periods, long enough to be a real chunk."
    chunks = [Chunk(0, text, text, "Backups", 1, 1, "text", 20)]
    clean = ParsedDocument("Rules", [], page_count=1)
    assert parse_quality(clean, chunks) == 1.0
    complex_fast = ParsedDocument("Rules", [], page_count=1, layout_hints=["1 page(s) with columns"])
    assert parse_quality(complex_fast, chunks) < 0.7
    garbled = [_chunk("ÿþ\x0c\x0e ¤¤¤ ¦¦¦ ¨¨¨ ¬¬¬ ®®® ±±± ´´´ ¸¸¸")]
    assert parse_quality(clean, garbled) < 0.5
    assert parse_quality(clean, []) == 0.0


async def test_duplicate_passages_across_files_are_indexed_once(
    settings: Settings, registry: FakeRegistry, corpus: Path
) -> None:
    shared = "Turn the compost pile every week so that air reaches the middle and the heap stays warm."
    for name in ("one", "two"):
        (corpus / f"{name}.md").write_text(f"# {name.title()}\n\n{shared}\n", encoding="utf-8")
    world = World(settings, registry)
    job = await world.build(world.create(corpus))
    assert job["summary"]["duplicates"] == 1
    reports = {row["uri"]: row["report"] for row in world.db.document_reports(job["summary"]["collection"])}
    assert reports["two.md"]["duplicates"] == 1 and reports["one.md"]["duplicates"] == 0


async def test_slow_files_time_out_alone(settings: Settings, registry: FakeRegistry, corpus: Path) -> None:
    write_pdf(corpus / "slow.pdf", [PdfPage().text(72, 700, 11, "A PDF that will not finish in time.")])
    world = World(settings.model_copy(update={"parse_timeout_s": 0.001}), registry)
    job = await world.build(world.create(corpus))
    assert job["status"] == "completed_with_errors"
    assert [f["document"] for f in job["failures"]] == ["slow.pdf"]
    assert "longer than" in job["failures"][0]["error"]


# Documents API: reports, chunks, edits


@pytest.fixture
def client(settings: Settings, registry: FakeRegistry) -> Any:
    app = create_app(settings, providers=registry, in_process_worker=True)  # type: ignore[arg-type]
    with TestClient(app) as test_client:
        yield test_client


def test_documents_and_chunk_edits(client: TestClient, corpus: Path) -> None:
    kb = create_kb(client, corpus)
    assert client.get(f"/api/kbs/{kb['id']}/documents").status_code == 409
    build(client, kb["id"])
    documents = client.get(f"/api/kbs/{kb['id']}/documents").json()
    assert len(documents) == 3
    report = documents[0]["report"]
    assert report["reader"] == "fast" and 0 < report["quality"] <= 1 and report["template"]
    uri = next(d["uri"] for d in documents if d["uri"].endswith(".md"))
    url = f"/api/kbs/{kb['id']}/documents/chunks"
    chunks = client.get(url, params={"uri": uri}).json()["chunks"]
    first = chunks[0]
    assert [c["ordinal"] for c in chunks] == sorted(c["ordinal"] for c in chunks) and not first["edited"]

    edited = client.patch(url, json={"uri": uri, "ordinal": first["ordinal"], "text": "Corrected text about mulch."})
    assert edited.status_code == 200, edited.text
    after = client.get(url, params={"uri": uri}).json()["chunks"][0]
    assert after["text"] == "Corrected text about mulch." and after["edited"]

    client.patch(url, json={"uri": uri, "ordinal": first["ordinal"], "excluded": True})
    assert client.get(url, params={"uri": uri}).json()["chunks"][0]["excluded"] is True
    events = [
        json.loads(line[6:])
        for line in client.post(
            f"/api/kbs/{kb['id']}/chat", json={"query": "corrected text about mulch"}
        ).text.splitlines()
        if line.startswith("data: ")
    ]
    sources = next((e["sources"] for e in events if e["type"] == "sources"), [])
    assert all("Corrected text" not in s.get("preview", "") for s in sources)

    assert client.delete(url, params={"uri": uri, "ordinal": first["ordinal"]}).status_code == 204
    restored = client.get(url, params={"uri": uri}).json()["chunks"][0]
    assert restored["text"] == first["text"] and not restored["excluded"] and not restored["edited"]
    assert client.get(url, params={"uri": "missing.md"}).status_code == 404


def test_validation_warns_about_office_files_and_enrichment_cost(client: TestClient, corpus: Path) -> None:
    (corpus / "plan.docx").write_bytes(b"not really a docx")
    kb = create_kb(client, corpus)
    config = {"source": {"path": str(corpus)}, "parsing": {"engine": "fast"}, "enrichment": {"context": True}}
    result = client.post(f"/api/kbs/{kb['id']}/configs/validate", json={"config": config}).json()
    stages = {warning["stage"]: warning["msg"] for warning in result["warnings"]}
    assert "1 Word, PowerPoint or Excel file(s) will be skipped" in stages["parsing"]
    assert "one model call per passage" in stages["enrichment"]
    assert result["checks"]["enrichment"]["ok"] is True
