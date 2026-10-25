from __future__ import annotations

from pathlib import Path

import pytest

from ragkit.config import ParsingConfig
from ragkit.parsing import parse_document, table_to_markdown
from tests.pdf_factory import PdfPage, write_pdf


def _book(path: Path, tables: bool = False) -> Path:
    pages = []
    for number in range(1, 4):
        page = PdfPage().text(72, 760, 9, "Field Notes Running Header").text(300, 30, 9, f"Page {number}")
        page.text(72, 700, 20, f"Chapter {number} Overview")
        page.text(72, 670, 11, f"This is the first paragraph of chapter {number} and it")
        page.text(72, 656, 11, "continues on a second line with a hyphen-")
        page.text(72, 642, 11, "ated word that should be joined.")
        page.text(72, 610, 11, "A second paragraph starts after a clear vertical gap.")
        if tables and number == 2:
            page.grid(72, 560, [120, 120], 20, [["Tool", "Use"], ["Fork", "Turning"], ["Sieve", "Screening"]])
        pages.append(page)
    return write_pdf(path, pages, title="Field Notes")


def test_pdf_headings_paragraphs_and_running_lines(tmp_path: Path) -> None:
    document = parse_document(_book(tmp_path / "book.pdf"), ParsingConfig())
    assert document.title == "Field Notes"
    assert document.page_count == 3
    headings = [b for b in document.blocks if b.kind == "heading"]
    assert [h.text for h in headings] == ["Chapter 1 Overview", "Chapter 2 Overview", "Chapter 3 Overview"]
    texts = [b.text for b in document.blocks if b.kind == "text"]
    assert any("hyphenated word that should be joined." in t for t in texts)
    assert sum("second paragraph" in t for t in texts) == 3
    assert not any("Running Header" in t or t.startswith("Page ") for t in texts)


def test_pdf_keeps_running_lines_when_disabled(tmp_path: Path) -> None:
    document = parse_document(_book(tmp_path / "book.pdf"), ParsingConfig(strip_headers_footers=False))
    assert any("Running Header" in b.text for b in document.blocks)


def test_pdf_tables_become_markdown_in_reading_order(tmp_path: Path) -> None:
    document = parse_document(_book(tmp_path / "book.pdf", tables=True), ParsingConfig())
    tables = [b for b in document.blocks if b.kind == "table"]
    assert len(tables) == 1 and document.table_count == 1
    assert tables[0].text.splitlines()[0] == "| Tool | Use |"
    assert "| Fork | Turning |" in tables[0].text
    kinds = [(b.kind, b.page) for b in document.blocks]
    assert kinds.index(("table", 2)) > kinds.index(("heading", 2))
    assert not any("Fork" in b.text for b in document.blocks if b.kind == "text")


def test_pdf_tables_can_be_dropped(tmp_path: Path) -> None:
    document = parse_document(_book(tmp_path / "book.pdf", tables=True), ParsingConfig(tables="off"))
    assert not any(b.kind == "table" for b in document.blocks)


def test_pdf_without_text_layer_warns_without_ocr_extra(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "scan.pdf", [PdfPage()])
    document = parse_document(path, ParsingConfig(ocr="auto"))
    assert document.blocks == []
    assert document.warnings and "ocr" in document.warnings[0]


def test_markdown_structure(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text(
        "# Title\n\nIntro line one\nline two\n\n## Part\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\nAfter.\n",
        encoding="utf-8",
    )
    document = parse_document(path, ParsingConfig())
    assert document.title == "Title"
    assert [(b.kind, b.text.splitlines()[0]) for b in document.blocks] == [
        ("heading", "Title"),
        ("text", "Intro line one line two"),
        ("heading", "Part"),
        ("table", "| a | b |"),
        ("text", "After."),
    ]
    assert document.blocks[2].level == 2


def test_plain_text_paragraphs(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("first\nparagraph\n\n\nsecond paragraph\n", encoding="utf-8")
    document = parse_document(path, ParsingConfig())
    assert document.title == "notes"
    assert [b.text for b in document.blocks] == ["first paragraph", "second paragraph"]


def test_unknown_extension(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no parser"):
        parse_document(tmp_path / "x.docx", ParsingConfig())


def test_corrupt_pdf_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    with pytest.raises(Exception):  # noqa: B017 - any parser error is acceptable; the build records it
        parse_document(path, ParsingConfig())


def test_table_to_markdown_pads_and_escapes() -> None:
    assert table_to_markdown([["a", "b|c"], ["1"], [None, None]]) == "| a | b\\|c |\n| --- | --- |\n| 1 |  |"
    assert table_to_markdown([]) == ""


def _body_page(page_number: int, *, header: str, headline: tuple[str, bool] | None = None) -> PdfPage:
    page = PdfPage().text(72, 740, 8, header)
    y = 700
    if headline:
        page.text(72, y, 11, headline[0], bold=headline[1])
        y -= 24
    for line in range(6):
        page.text(72, y - 14 * line, 11, f"Body text line {line} on page {page_number} keeps the reader busy here.")
    return page


def test_bold_body_size_headings_and_emphasis(tmp_path: Path) -> None:
    pages = [
        _body_page(1, header="t h e g u i d e i", headline=("PRINCIPLE 1", True)),
        _body_page(2, header="t h e g u i d e ii", headline=("Give Honest Appreciation", True)),
        _body_page(3, header="t h e g u i d e iii").text(72, 560, 11, "Dear Sir:", bold=True),
    ]
    pages[2].text(72, 520, 11, "This bold line opens an emphasised", bold=True)
    pages[2].text(72, 506, 11, "paragraph that continues in bold.", bold=True)
    document = parse_document(write_pdf(tmp_path / "bold.pdf", pages), ParsingConfig())
    headings = [b.text for b in document.blocks if b.kind == "heading"]
    assert headings == ["PRINCIPLE 1", "Give Honest Appreciation"]
    assert not any("guide" in b.text for b in document.blocks)  # letter-spaced running header with roman numerals


def test_contents_heading_is_not_a_parent_section(tmp_path: Path) -> None:
    page = PdfPage().text(72, 720, 20, "Contents").text(72, 690, 11, "Part One ...... 3")
    page.text(72, 640, 16, "Part One").text(72, 610, 11, "The first part begins with a full sentence.")
    document = parse_document(write_pdf(tmp_path / "toc.pdf", [page]), ParsingConfig())
    assert [b.text for b in document.blocks if b.kind == "heading"] == ["Part One"]


def test_overprinted_text_is_kept_once(tmp_path: Path) -> None:
    page = PdfPage().text(72, 700, 11, "Fake bold text").text(72.3, 700, 11, "Fake bold text")
    page.text(72, 680, 11, "Normal line follows.")
    document = parse_document(write_pdf(tmp_path / "overprint.pdf", [page]), ParsingConfig())
    assert document.blocks[0].text.startswith("Fake bold text Normal")
    assert "Fake bold text Fake" not in document.blocks[0].text
