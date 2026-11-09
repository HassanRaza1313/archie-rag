"""Readers for CSV, JSON, email, EPUB, OpenDocument, images and transcripts; plus erasure of derived data."""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

from ragkit import formats
from ragkit.config import KnowledgeBaseConfig, ParsingConfig
from ragkit.parsing import parse_document
from ragkit.storage import Database

CONFIG = ParsingConfig()


def test_csv_becomes_tables_that_repeat_the_header(tmp_path: Path) -> None:
    rows = "\n".join(f"SKU-{i},Widget {i},{i * 2}" for i in range(120))
    path = tmp_path / "stock.csv"
    path.write_text(f"sku,name,qty\n{rows}\n", encoding="utf-8")
    document = parse_document(path, CONFIG)
    assert [b.kind for b in document.blocks] == ["table"] * 3 and document.table_count == 3
    assert all(b.text.startswith("| sku | name | qty |") for b in document.blocks)
    assert "| SKU-119 | Widget 119 | 238 |" in document.blocks[-1].text
    (tmp_path / "empty.tsv").write_text("", encoding="utf-8")
    assert parse_document(tmp_path / "empty.tsv", CONFIG).blocks == []


def test_json_reads_as_labelled_lines(tmp_path: Path) -> None:
    path = tmp_path / "plans.json"
    path.write_text('{"plans": [{"name": "Team", "price": 12}], "currency": "EUR"}', encoding="utf-8")
    blocks = parse_document(path, CONFIG).blocks
    assert [(b.kind, b.text) for b in blocks] == [
        ("heading", "plans"),
        ("text", "1 › name: Team\n1 › price: 12"),
        ("heading", "currency"),
        ("text", "EUR"),
    ]


def test_email_keeps_sender_subject_and_body(tmp_path: Path) -> None:
    path = tmp_path / "offer.eml"
    path.write_bytes(
        b"From: Ada <ada@example.com>\r\nTo: bo@example.com\r\nSubject: Renewal offer\r\n"
        b"Date: Mon, 2 Nov 2026 10:00:00 +0000\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        b"The renewal price is 900 euros.\r\n\r\nIt is valid until December.\r\n"
    )
    document = parse_document(path, CONFIG)
    assert document.title == "Renewal offer"
    texts = [b.text for b in document.blocks]
    assert "From: Ada <ada@example.com>" in texts[1] and "The renewal price is 900 euros." in texts


def _epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/book.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<metadata><dc:title>Field Guide</dc:title></metadata><manifest>"
            '<item id="b" href="two.xhtml"/><item id="a" href="one.xhtml"/></manifest>'
            '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>',
        )
        archive.writestr("OEBPS/one.xhtml", "<html><body><h1>Birds</h1><p>Robins sing at dawn.</p></body></html>")
        archive.writestr("OEBPS/two.xhtml", "<html><body><h1>Trees</h1><p>Oaks live long.</p></body></html>")


def test_epub_follows_the_reading_order(tmp_path: Path) -> None:
    _epub(tmp_path / "guide.epub")
    document = parse_document(tmp_path / "guide.epub", CONFIG)
    assert document.title == "Field Guide" and document.page_count == 2
    assert [(b.text, b.page) for b in document.blocks] == [
        ("Birds", 1),
        ("Robins sing at dawn.", 1),
        ("Trees", 2),
        ("Oaks live long.", 2),
    ]


def test_archives_that_unpack_too_large_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _epub(tmp_path / "guide.epub")
    monkeypatch.setattr(formats, "_MAX_UNZIPPED", 100)
    with pytest.raises(ValueError, match="512 MB"):
        parse_document(tmp_path / "guide.epub", CONFIG)


def test_opendocument_text_and_sheets(tmp_path: Path) -> None:
    text, table = "urn:oasis:names:tc:opendocument:xmlns:text:1.0", "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    content = (
        f'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        f'xmlns:text="{text}" xmlns:table="{table}"><office:body><office:text>'
        '<text:h text:outline-level="1">Leave policy</text:h><text:p>Staff get 25 days.</text:p>'
        '<table:table table:name="Allowance"><table:table-row>'
        "<table:table-cell><text:p>Years</text:p></table:table-cell>"
        "<table:table-cell><text:p>Days</text:p></table:table-cell></table:table-row><table:table-row>"
        "<table:table-cell><text:p>5+</text:p></table:table-cell><table:table-cell><text:p>28</text:p></table:table-cell>"
        "</table:table-row></table:table></office:text></office:body></office:document-content>"
    )
    with zipfile.ZipFile(tmp_path / "policy.odt", "w") as archive:
        archive.writestr("content.xml", content)
    document = parse_document(tmp_path / "policy.odt", CONFIG)
    assert document.title == "Leave policy"
    assert [b.kind for b in document.blocks] == ["heading", "text", "heading", "table"]
    assert "| 5+ | 28 |" in document.blocks[-1].text


def test_images_are_read_with_ocr(tmp_path: Path) -> None:
    pytest.importorskip("rapidocr_onnxruntime")
    from PIL import Image, ImageDraw, ImageFont

    picture = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(picture).text((30, 60), "Fire exit on level two", fill="black", font=ImageFont.load_default(48))
    picture.save(tmp_path / "sign.png")
    document = parse_document(tmp_path / "sign.png", CONFIG)
    assert "exit" in " ".join(b.text for b in document.blocks).lower() and document.ocr_pages == 1


def test_transcripts_get_a_timestamp_every_two_minutes() -> None:
    blocks = formats.transcript_blocks([(0.0, " Welcome."), (65.0, " Agenda."), (130.0, " Budget."), (3725.0, " Bye.")])
    assert [(b.kind, b.text) for b in blocks] == [
        ("heading", "0:00"),
        ("text", "Welcome. Agenda."),
        ("heading", "2:10"),
        ("text", "Budget."),
        ("heading", "1:02:05"),
        ("text", "Bye."),
    ]


def test_deleting_a_chatbot_erases_what_was_derived_from_it(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    kb_id = db.create_kb("hr", KnowledgeBaseConfig())
    db.enrichment_put("k1", {"context": "salary bands"}, kb_id)
    db.audit("ada@x.io", "signed in", None, 200)
    db.delete_kb(kb_id)
    assert db.enrichment_get("k1") is None
    assert db.purge_audit(time.time() + 1) == 1 and db.audit_log(10) == []
