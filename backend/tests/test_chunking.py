from __future__ import annotations

import itertools

from ragkit.chunking import chunk_document
from ragkit.config import ChunkingConfig
from ragkit.parsing import Block, ParsedDocument
from ragkit.textutil import estimate_tokens


def sentence(index: int) -> str:
    return f"Sentence number {index} talks about composting and soil structure in some detail."


def document(*blocks: Block, title: str = "Guide") -> ParsedDocument:
    return ParsedDocument(title=title, blocks=list(blocks))


def text(value: str, page: int = 1, page_end: int | None = None) -> Block:
    return Block("text", value, page, page_end or page)


def heading(value: str, level: int = 1, page: int = 1) -> Block:
    return Block("heading", value, page, page, level)


def test_paragraphs_pack_up_to_size_with_section_paths() -> None:
    doc = document(
        heading("Guide"),
        heading("Soil", 2),
        text(sentence(1)),
        text(sentence(2)),
        heading("Water", 2),
        text(" ".join(sentence(i) for i in range(3, 6))),
    )
    chunks = chunk_document(doc, ChunkingConfig(chunk_size=128, overlap_pct=0))
    assert [c.section for c in chunks] == ["Guide › Soil", "Guide › Water"]
    assert chunks[0].text == f"{sentence(1)} {sentence(2)}"
    assert all(c.tokens <= 128 for c in chunks)
    assert [c.ordinal for c in chunks] == [0, 1]


def test_long_paragraph_splits_on_sentences_with_overlap() -> None:
    paragraph = " ".join(sentence(i) for i in range(1, 30))
    chunks = chunk_document(document(text(paragraph)), ChunkingConfig(chunk_size=128, overlap_pct=20))
    assert len(chunks) > 3
    for previous, current in itertools.pairwise(chunks):
        last_sentence = previous.text.rsplit(". ", 1)[-1]
        assert current.text.startswith(last_sentence.split(" Sentence")[0][:20])
    assert all(estimate_tokens(c.text) <= 128 * 1.25 for c in chunks)


def test_no_overlap_when_disabled() -> None:
    paragraph = " ".join(sentence(i) for i in range(1, 20))
    chunks = chunk_document(document(text(paragraph)), ChunkingConfig(chunk_size=128, overlap_pct=0))
    joined = " ".join(c.text for c in chunks)
    assert joined.count("Sentence number 5 ") == 1


def test_chunks_cross_pages_and_track_span() -> None:
    doc = document(heading("Part"), text(sentence(1), page=1), text(sentence(2), page=2, page_end=3))
    chunks = chunk_document(doc, ChunkingConfig(chunk_size=512))
    assert len(chunks) == 1
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 3)


def test_tiny_sections_merge_forward() -> None:
    doc = document(heading("A"), text("Tiny."), heading("B"), text(sentence(1)))
    chunks = chunk_document(doc, ChunkingConfig(chunk_size=256))
    assert len(chunks) == 1 and chunks[0].section == "A"
    assert chunks[0].text == f"Tiny. {sentence(1)}"


def test_tables_are_separate_and_split_with_header() -> None:
    rows = "\n".join(f"| item {i} | value {i} with some descriptive text |" for i in range(60))
    table = Block("table", "| name | value |\n| --- | --- |\n" + rows, 2, 2)
    chunks = chunk_document(document(text(sentence(1)), table), ChunkingConfig(chunk_size=128))
    tables = [c for c in chunks if c.content_type == "table"]
    assert chunks[0].content_type == "text"
    assert len(tables) > 1
    assert all(c.text.startswith("| name | value |\n| --- | --- |") for c in tables)


def test_unbreakable_text_is_hard_split() -> None:
    blob = " ".join(["word"] * 2000)
    chunks = chunk_document(document(text(blob)), ChunkingConfig(chunk_size=100, overlap_pct=0))
    assert len(chunks) > 10 and all(c.tokens <= 100 for c in chunks)


def test_embed_text_gets_contextual_header() -> None:
    doc = document(heading("Guide"), heading("Soil", 2), text(sentence(1)), title="Guide")
    with_header = chunk_document(doc, ChunkingConfig())[0]
    without = chunk_document(doc, ChunkingConfig(section_headers=False))[0]
    assert with_header.embed_text.startswith("Guide › Guide › Soil\n\n")
    assert without.embed_text == without.text


def test_fixed_windows_cover_text_with_overlap() -> None:
    doc = document(*(text(sentence(i), page=i) for i in range(1, 40)))
    chunks = chunk_document(doc, ChunkingConfig(strategy="fixed", chunk_size=64, overlap_pct=25))
    assert len(chunks) > 5
    assert chunks[0].page_start == 1 and chunks[-1].page_end == 39
    assert all(len(c.text) <= 64 * 4 for c in chunks)
    combined = " ".join(c.text for c in chunks)
    assert all(f"number {i} " in combined for i in range(1, 40))


def test_empty_document_has_no_chunks() -> None:
    assert chunk_document(document(), ChunkingConfig()) == []
    assert chunk_document(document(), ChunkingConfig(strategy="fixed")) == []
