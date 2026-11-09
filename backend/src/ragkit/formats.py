"""Readers for everyday formats beyond PDF and Office: spreadsheets as CSV, JSON, email, EPUB, OpenDocument,
images (OCR) and audio or video (local speech-to-text). Each returns the same ``ParsedDocument`` as the PDF reader,
so chunking, enrichment and search treat them alike.

Archives (EPUB, OpenDocument) are size-checked before they are opened, so a zip bomb fails on its own.
"""

from __future__ import annotations

import csv
import email
import io
import json
import zipfile
from collections.abc import Callable
from email import policy
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree
from xml.parsers import expat

from ragkit.config import ParsingConfig
from ragkit.parsing import Block, ParsedDocument, _HtmlBlocks, _Line, _lines_to_blocks, table_to_markdown

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
MEDIA_TYPES = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".mov", ".webm", ".mkv"}
_MAX_UNZIPPED = 512 * 1024 * 1024
_ROWS_PER_TABLE = 50  # each table block repeats the header, so every passage of rows reads on its own
_SECONDS_PER_SECTION = 120  # a timestamp heading every two minutes of speech: citations say where to listen


def _xml(data: bytes) -> ElementTree.Element:
    """Parse XML from a document. Expat 2.4.1+ (Python 3.11+) stops entity-expansion bombs, and ElementTree never
    fetches external entities, so the standard parser is safe here."""
    assert expat.version_info >= (2, 4, 1), "this Python's expat cannot stop entity-expansion attacks"
    return ElementTree.fromstring(data)  # noqa: S314


def _open_zip(path: Path) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(path)
    if sum(info.file_size for info in archive.infolist()) > _MAX_UNZIPPED:
        archive.close()
        raise ValueError("the archive unpacks to more than 512 MB, so it was skipped")
    return archive


def _html_blocks(markup: str) -> tuple[str, list[Block]]:
    reader = _HtmlBlocks()
    reader.feed(markup)
    reader.close()
    reader._flush()
    return " ".join(reader.title.split()), reader.blocks


# Tables and data


def parse_csv(path: Path, config: ParsingConfig) -> ParsedDocument:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    delimiter = "\t" if path.suffix.lower() == ".tsv" else (csv.Sniffer().sniff(text[:4096]).delimiter if text else ",")
    rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(cell.strip() for cell in row)]
    if not rows:
        return ParsedDocument(path.stem, [])
    header, body = rows[0], rows[1:] or [[]]
    blocks = [
        Block("table", table_to_markdown([header, *body[start : start + _ROWS_PER_TABLE]]), 1, 1)
        for start in range(0, len(body), _ROWS_PER_TABLE)
    ]
    return ParsedDocument(path.stem, blocks, table_count=len(blocks))


def _flatten(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        return [line for key, item in value.items() for line in _flatten(item, f"{prefix} › {key}" if prefix else key)]
    if isinstance(value, list):
        return [
            line
            for index, item in enumerate(value, start=1)
            for line in _flatten(item, f"{prefix} {index}" if prefix else str(index))
        ]
    return [f"{prefix}: {value}" if prefix else str(value)]


def parse_json(path: Path, config: ParsingConfig) -> ParsedDocument:
    """Each top-level key (or list item) becomes a section of 'path › to › field: value' lines."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    items = data.items() if isinstance(data, dict) else ((str(i), item) for i, item in enumerate(data, start=1))
    blocks = []
    for key, item in items if isinstance(data, dict | list) else [("", data)]:
        if key:
            blocks.append(Block("heading", str(key), 1, 1, 1))
        lines = _flatten(item)
        blocks.extend(Block("text", "\n".join(lines[start : start + 40]), 1, 1) for start in range(0, len(lines), 40))
    return ParsedDocument(path.stem, blocks)


# Email


def parse_email(path: Path, config: ParsingConfig) -> ParsedDocument:
    with path.open("rb") as handle:
        message = email.message_from_binary_file(handle, policy=policy.default)
    subject = str(message.get("subject") or path.stem)
    header = "\n".join(f"{name}: {message[name]}" for name in ("From", "To", "Cc", "Date") if message[name])
    blocks = [Block("heading", subject, 1, 1, 1)] + ([Block("text", header, 1, 1)] if header else [])
    body = message.get_body(preferencelist=("plain", "html"))
    if body is not None:
        content = body.get_content()
        if body.get_content_subtype() == "html":
            blocks.extend(_html_blocks(content)[1])
        else:
            paragraphs = [" ".join(p.split()) for p in content.split("\n\n") if p.strip()]
            blocks.extend(Block("text", p, 1, 1) for p in paragraphs)
    attachments = [name for part in message.iter_attachments() if (name := part.get_filename())]
    warnings = [f"attachments were not read: {', '.join(attachments)}"] if attachments else []
    if attachments:
        blocks.append(Block("text", "Attachments: " + ", ".join(attachments), 1, 1))
    return ParsedDocument(subject, blocks, warnings=warnings)


# EPUB and OpenDocument


def parse_epub(path: Path, config: ParsingConfig) -> ParsedDocument:
    """Chapters in reading order (the package's spine), each read by the HTML reader."""
    with _open_zip(path) as archive:
        container = _xml(archive.read("META-INF/container.xml"))
        package_path = next(el.get("full-path") for el in container.iter() if el.tag.endswith("rootfile"))
        package = _xml(archive.read(str(package_path)))
        base = PurePosixPath(str(package_path)).parent
        manifest = {el.get("id"): el.get("href") for el in package.iter() if el.tag.endswith("}item")}
        spine = [manifest.get(el.get("idref")) for el in package.iter() if el.tag.endswith("itemref")]
        title = next((el.text for el in package.iter() if el.tag.endswith("}title") and el.text), path.stem)
        blocks: list[Block] = []
        for number, href in enumerate((h for h in spine if h), start=1):
            _, chapter = _html_blocks(archive.read(str(base / href)).decode("utf-8", errors="replace"))
            blocks.extend(Block(b.kind, b.text, number, number, b.level) for b in chapter)
    return ParsedDocument(str(title), blocks, page_count=max(1, len(spine)))


_ODF_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODF_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODF_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"


def parse_opendocument(path: Path, config: ParsingConfig) -> ParsedDocument:
    """Writer headings and paragraphs, Calc sheets as tables, Impress slides as pages."""
    with _open_zip(path) as archive:
        root = _xml(archive.read("content.xml"))
    blocks: list[Block] = []
    slide = 0

    def text_of(element: ElementTree.Element) -> str:
        return " ".join("".join(element.itertext()).split())

    def walk(element: ElementTree.Element) -> None:
        nonlocal slide
        for child in element:
            if child.tag == f"{_ODF_DRAW}page":
                slide += 1
                blocks.append(Block("heading", child.get(f"{_ODF_DRAW}name") or f"Slide {slide}", slide, slide, 1))
                walk(child)
            elif child.tag == f"{_ODF_TEXT}h":
                level = int(child.get(f"{_ODF_TEXT}outline-level") or 1)
                if text := text_of(child):
                    blocks.append(Block("heading", text, max(1, slide), max(1, slide), min(3, level)))
            elif child.tag == f"{_ODF_TEXT}p":
                if text := text_of(child):
                    blocks.append(Block("text", text, max(1, slide), max(1, slide)))
            elif child.tag == f"{_ODF_TABLE}table":
                if name := child.get(f"{_ODF_TABLE}name"):
                    blocks.append(Block("heading", name, 1, 1, 2))
                rows = [
                    [text_of(cell) for cell in row.iter(f"{_ODF_TABLE}table-cell")]
                    for row in child.iter(f"{_ODF_TABLE}table-row")
                ]
                rows = [row for row in rows if any(row)]
                for start in range(1, max(2, len(rows)), _ROWS_PER_TABLE):
                    if markdown := table_to_markdown([rows[0], *rows[start : start + _ROWS_PER_TABLE]] if rows else []):
                        blocks.append(Block("table", markdown, 1, 1))
            else:
                walk(child)

    walk(root)
    title = next((b.text for b in blocks if b.kind == "heading"), path.stem)
    return ParsedDocument(title, blocks, page_count=max(1, slide), table_count=sum(b.kind == "table" for b in blocks))


# Images


def parse_image(path: Path, config: ParsingConfig) -> ParsedDocument:
    try:
        import numpy as np
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise ValueError("images are read with OCR: install the 'ocr' extra") from exc
    with Image.open(path) as picture:
        image = np.asarray(picture.convert("RGB"))
    result, _ = RapidOCR()(image)
    lines = [
        _Line(text, box[2][1] - box[0][1], box[0][1], box[2][1], 1, left=box[0][0])
        for box, text, _score in sorted(result or [], key=lambda item: (item[0][0][1], item[0][0][0]))
    ]
    warnings = [] if lines else ["no text was found in this image"]
    blocks = _lines_to_blocks(lines, []) if lines else []
    return ParsedDocument(path.stem, blocks, ocr_pages=1, warnings=warnings)


# Audio and video


def _clock(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes}:{rest:02d}"


def transcript_blocks(segments: list[tuple[float, str]]) -> list[Block]:
    """A timestamp heading every two minutes, then that stretch of speech: a cited passage says where to listen."""
    blocks: list[Block] = []
    section_start = -float(_SECONDS_PER_SECTION)
    paragraph: list[str] = []
    for start, text in segments:
        if start - section_start >= _SECONDS_PER_SECTION:
            if paragraph:
                blocks.append(Block("text", " ".join(paragraph), 1, 1))
                paragraph = []
            section_start = start
            blocks.append(Block("heading", _clock(start), 1, 1, 2))
        if text.strip():
            paragraph.append(text.strip())
    if paragraph:
        blocks.append(Block("text", " ".join(paragraph), 1, 1))
    return blocks


def parse_media(path: Path, config: ParsingConfig) -> ParsedDocument:
    """Speech to text on this machine (faster-whisper); video files are read for their sound."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ValueError("audio and video are transcribed locally: install the 'audio' extra") from exc
    model = WhisperModel(config.speech_model, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(path), vad_filter=True)
    spoken = [(segment.start, segment.text) for segment in segments]
    document = ParsedDocument(path.stem, transcript_blocks(spoken))
    if not spoken:
        document.warnings.append("no speech was found")
    document.warnings.append(f"transcribed with the {config.speech_model} speech model ({info.language})")
    return document


READERS: dict[str, Callable[[Path, ParsingConfig], ParsedDocument]] = {
    ".csv": parse_csv,
    ".tsv": parse_csv,
    ".json": parse_json,
    ".eml": parse_email,
    ".epub": parse_epub,
    ".odt": parse_opendocument,
    ".ods": parse_opendocument,
    ".odp": parse_opendocument,
    **dict.fromkeys(IMAGE_TYPES, parse_image),
    **dict.fromkeys(MEDIA_TYPES, parse_media),
}
