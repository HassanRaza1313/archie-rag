"""Document parsing into structured blocks (headings, paragraphs, tables) with page spans.

PDF text comes from pdfium (fast, permissive licence): larger or bold short lines become headings, layout gaps
split paragraphs, and repeated margin lines (running headers, footers, page numbers) are dropped. Ruled tables are
found with pdfplumber, only on pages that contain drawn paths, and become markdown. Pages without a text layer are
OCR'd when the optional ``ocr`` extra is installed. Add a format by registering a parser in ``PARSERS``.
"""

from __future__ import annotations

import itertools
import re
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ragkit.config import ParsingConfig

BlockKind = Literal["heading", "text", "table"]


@dataclass
class Block:
    kind: BlockKind
    text: str
    page: int
    page_end: int
    level: int = 0


@dataclass
class ParsedDocument:
    title: str
    blocks: list[Block]
    page_count: int = 1
    ocr_pages: int = 0
    table_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Line:
    text: str
    size: float
    top: float
    bottom: float
    page: int
    margin: bool = False  # inside the top/bottom 8% of the page
    edge: bool = False  # inside the top/bottom 15%: where running headers can sit
    bold: bool = False
    left: float = 0.0


_SENTENCE_END = re.compile(r"[.!?:;\"'”’)\]]$")


def parse_document(path: Path, config: ParsingConfig) -> ParsedDocument:
    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(f"no parser for '{path.suffix}' files")
    return parser(path, config)


# PDF


_BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi", re.IGNORECASE)
_PDFIUM_HYPHEN = "\x02"
_CONTENTS_HEADING = re.compile(r"^\s*(table of )?contents\s*$", re.IGNORECASE)
_TABLE_MIN_PATHS = 4  # pages with fewer drawn paths cannot hold a ruled table, so the slow table finder is skipped
Box = tuple[float, float, float, float]  # x0, top, x1, bottom (top-down page coordinates)


@dataclass
class _Char:
    index: int
    text: str
    size: float
    left: float
    top: float
    bottom: float
    bold: bool


def parse_pdf(path: Path, config: ParsingConfig) -> ParsedDocument:
    import pypdfium2 as pdfium

    lines: list[_Line] = []
    tables: list[tuple[int, float, Block]] = []  # (page, top, block) for placing tables in reading order
    ocr_pages = 0
    warnings: list[str] = []
    document = pdfium.PdfDocument(path)
    plumber: Any = None
    try:
        page_count = len(document)
        title = str(document.get_metadata_dict().get("Title") or "").strip()
        for number in range(1, page_count + 1):
            page = document[number - 1]
            height = float(page.get_height())
            boxes: list[Box] = []
            if config.tables == "markdown" and _path_count(page) >= _TABLE_MIN_PATHS:
                if plumber is None:
                    import pdfplumber

                    plumber = pdfplumber.open(path)
                for table in plumber.pages[number - 1].find_tables():
                    x0, top, x1, bottom = (float(value) for value in table.bbox)
                    boxes.append((x0, top, x1, bottom))
                    if markdown := table_to_markdown(table.extract()):
                        tables.append((number, top, Block("table", markdown, number, number)))
            page_lines = [] if config.ocr == "force" else _text_lines(page, number, height, boxes)
            if config.ocr == "force" or (config.ocr == "auto" and not page_lines):
                ocr_lines, warning = _ocr_page(page, number, height)
                if ocr_lines:
                    ocr_pages += 1
                    page_lines = ocr_lines
                elif warning and warning not in warnings:
                    warnings.append(warning)
            lines.extend(page_lines)
    finally:
        document.close()
        if plumber is not None:
            plumber.close()
    if config.strip_headers_footers:
        lines = _drop_running_lines(lines, page_count)
    blocks = _lines_to_blocks(lines, tables)
    if not title:
        title = next((b.text for b in blocks if b.kind == "heading"), path.stem)
    return ParsedDocument(title, blocks, page_count, ocr_pages, len(tables), warnings)


def _path_count(page: Any) -> int:
    import pypdfium2.raw as raw

    return sum(1 for _ in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH], max_depth=1))


def _text_lines(page: Any, number: int, height: float, boxes: list[Box]) -> list[_Line]:
    """Group pdfium characters into lines carrying size, boldness and top-down coordinates.

    Characters drawn twice at the same spot (fake bold, shadow layers) are kept once; characters inside table
    boxes are skipped because the table is emitted separately.
    """
    import pypdfium2.raw as raw

    textpage = page.get_textpage()
    handle = textpage.raw
    lines: list[_Line] = []
    chars: list[_Char] = []

    def finish() -> None:
        visible = [c for c in chars if not c.text.isspace() and c.text != _PDFIUM_HYPHEN]
        # pdfium joins a hyphenated line break into one line and marks the hyphen with U+0002.
        text = " ".join("".join(c.text for c in chars).replace(_PDFIUM_HYPHEN, "").split())
        text = _LETTER_SPACED.sub(lambda match: match.group(1).replace(" ", ""), text)
        if visible and text:
            top, bottom = min(c.top for c in visible), max(c.bottom for c in visible)
            bold = sum(c.bold for c in visible) >= 0.9 * len(visible) or bool(
                _BOLD_FONT.search(_font_name(raw, handle, visible[0].index))
            )
            margin, edge = _bands(top, bottom, height)
            size = statistics.median(c.size for c in visible)
            left = min(c.left for c in visible)
            lines.append(_Line(text, size, top, bottom, number, margin=margin, edge=edge, bold=bold, left=left))
        chars.clear()

    try:
        for index in range(textpage.count_chars()):
            text = chr(raw.FPDFText_GetUnicode(handle, index))
            if text in "\r\n":
                if text == "\n":
                    finish()
                continue
            left, bottom_up, right, top_up = textpage.get_charbox(index)
            top, bottom = height - top_up, height - bottom_up
            if not text.isspace():
                center_x, center_y = (left + right) / 2, (top + bottom) / 2
                if any(x0 <= center_x <= x1 and y0 <= center_y <= y1 for x0, y0, x1, y1 in boxes):
                    continue
                if any(c.text == text and abs(c.left - left) < 1.5 and abs(c.top - top) < 1.5 for c in chars[-3:]):
                    continue
            weight = raw.FPDFText_GetFontWeight(handle, index)
            chars.append(
                _Char(index, text, float(raw.FPDFText_GetFontSize(handle, index)), left, top, bottom, weight >= 600)
            )
        finish()
    finally:
        textpage.close()
    return lines


def _bands(top: float, bottom: float, height: float) -> tuple[bool, bool]:
    return top < height * 0.08 or bottom > height * 0.92, top < height * 0.15 or bottom > height * 0.85


def _font_name(raw: Any, handle: Any, index: int) -> str:
    """Font name of one character; the weight is missing for many standard fonts ("Helvetica-Bold")."""
    import ctypes

    buffer = ctypes.create_string_buffer(128)
    flags = ctypes.c_int()
    raw.FPDFText_GetFontInfo(handle, index, buffer, len(buffer), ctypes.byref(flags))
    return buffer.value.decode("latin-1", errors="replace")


_PAGE_NUMBER = re.compile(r"^\W*(page\s*)?(\d+|[ivxlcdm]+)(\s*(of|/)\s*\d+)?\W*$", re.IGNORECASE)
_RUNNING_MIN_PAGES = 3


def _running_key(text: str) -> str:
    """Normalise a margin line so a running header matches across pages: ignore letter-spacing, digits and
    roman-numeral page numbers at either end ("viii contents" and "ix contents" share a key)."""
    compact = re.sub(r"[\W\d_]+", "", text.lower())
    return re.sub(r"^[ivxlcdm]+|[ivxlcdm]+$", "", compact)


def _drop_running_lines(lines: list[_Line], page_count: int) -> list[_Line]:
    """Drop running headers/footers: short lines repeated on several pages, either inside the top/bottom margin
    band or at the same vertical position (some PDFs place them well inside the page box)."""

    def placed(line: _Line) -> tuple[str, int]:
        return _running_key(line.text), round(line.top / 6)

    # Running headers are set at body size or smaller; larger repeated lines are chapter headings ("Chapter 3").
    sizes = sorted(line.size for line in lines if line.size)
    small_limit = sizes[len(sizes) // 2] * 1.05 if sizes else 0.0
    in_margin: dict[str, set[int]] = {}
    at_position: dict[tuple[str, int], set[int]] = {}
    if page_count >= _RUNNING_MIN_PAGES:
        for line in lines:
            if len(line.text.split()) > 12 and not line.margin:
                continue
            if line.margin:
                in_margin.setdefault(_running_key(line.text), set()).add(line.page)
            at_position.setdefault(placed(line), set()).add(line.page)

    def is_running(line: _Line) -> bool:
        if line.margin and _PAGE_NUMBER.match(line.text):
            return True
        if line.margin and len(in_margin.get(_running_key(line.text), ())) >= _RUNNING_MIN_PAGES:
            return True
        return (
            line.edge
            and line.size <= small_limit
            and len(line.text.split()) <= 12
            and len(at_position.get(placed(line), ())) >= _RUNNING_MIN_PAGES
        )

    return [line for line in lines if not is_running(line)]


def _lines_to_blocks(lines: list[_Line], tables: list[tuple[int, float, Block]]) -> list[Block]:
    sized = [(round(line.size * 2) / 2, len(line.text)) for line in lines if line.size]
    weights: Counter[float] = Counter()
    for size, length in sized:
        weights[size] += length
    body_size = weights.most_common(1)[0][0] if weights else 0.0
    heading_sizes = sorted({size for size, _ in sized if body_size and size >= body_size * 1.18}, reverse=True)
    body = [line for line in lines if not line.margin and round(line.size * 2) / 2 == body_size]
    gaps = sorted(b.top - a.bottom for a, b in itertools.pairwise(body) if a.page == b.page and b.top > a.bottom)
    line_gap = gaps[len(gaps) // 4] if gaps else 4.0  # lower quartile: the ordinary gap between wrapped lines
    paragraph_gap = line_gap * 1.6 + 2
    left_margins: dict[int, float] = {}
    for page in {line.page for line in body}:
        lefts = Counter(round(line.left) for line in body if line.page == page)
        left_margins[page] = float(lefts.most_common(1)[0][0])

    blocks: list[Block] = []
    pending_tables = sorted(tables, key=lambda t: (t[0], t[1]))
    paragraph: list[_Line] = []

    def flush() -> None:
        if paragraph:
            blocks.append(Block("text", _join_lines(paragraph), paragraph[0].page, paragraph[-1].page))
            paragraph.clear()

    for index, line in enumerate(lines):
        while pending_tables and pending_tables[0][:2] <= (line.page, line.top):
            flush()
            blocks.append(pending_tables.pop(0)[2])
        if _CONTENTS_HEADING.match(line.text):
            flush()  # a table of contents is navigation, never the parent section of the whole book
            continue
        rounded = round(line.size * 2) / 2
        sized_heading = rounded in heading_sizes and _looks_like_heading(line.text)
        following = lines[index + 1] if index + 1 < len(lines) else None
        # A bold line is a heading when it stands alone; a run of bold lines is an emphasised paragraph.
        stands_alone = line.text.isupper() or following is None or not following.bold or following.page != line.page
        bold_heading = (
            line.bold
            and rounded >= body_size
            and stands_alone
            and _looks_like_heading(line.text, max_words=12)
            and _is_title_like(line.text)
        )
        if sized_heading or bold_heading:
            flush()
            level = min(3, heading_sizes.index(rounded) + 1 if sized_heading else len(heading_sizes) + 1)
            if blocks and blocks[-1].kind == "heading" and blocks[-1].level == level and blocks[-1].page == line.page:
                blocks[-1].text += " " + line.text  # a heading wrapped over two lines
            else:
                blocks.append(Block("heading", line.text, line.page, line.page, level))
            continue
        previous = lines[index - 1] if index else None
        if paragraph and previous is not None:
            new_page = line.page != previous.page
            sentence_ended = bool(_SENTENCE_END.search(previous.text))
            indented = line.left > left_margins.get(line.page, line.left) + 6
            if (
                (new_page and sentence_ended)
                or (not new_page and line.top - previous.bottom > paragraph_gap)
                or (indented and sentence_ended)
            ):
                flush()
        paragraph.append(line)
    flush()
    blocks.extend(table for _, _, table in pending_tables)
    return blocks


def _looks_like_heading(text: str, max_words: int = 16) -> bool:
    words = text.split()
    return (
        0 < len(words) <= max_words
        and len(text) <= 120
        and not text.endswith((".", ",", ";"))
        and any(c.isalpha() for c in text)
    )


_MINOR_WORDS = frozenset(
    ("a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "with", "vs")
)
_LETTER_SPACED = re.compile(r"(?<!\S)((?:\w ){3,}\w)(?!\S)")  # "c o n t e n t s": tracked small caps


def _is_title_like(text: str) -> bool:
    """Upper case or Title Case (minor words excepted), and not a lead-in such as 'Dear Sir:'."""
    if text.rstrip().endswith(":"):
        return False
    words = [w for w in re.findall(r"[^\W\d_][\w'’-]*", text) if w.lower() not in _MINOR_WORDS]
    return bool(words) and sum(w[0].isupper() for w in words) >= 0.8 * len(words)


def _join_lines(lines: list[_Line]) -> str:
    text = ""
    for line in lines:
        if text.endswith("-") and line.text[:1].islower():
            text = text[:-1] + line.text
        else:
            text = f"{text} {line.text}" if text else line.text
    return text


def table_to_markdown(rows: list[list[str | None]]) -> str:
    cleaned = [[(cell or "").replace("\n", " ").replace("|", "\\|").strip() for cell in row] for row in rows if row]
    cleaned = [row for row in cleaned if any(row)]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    header, *body = cleaned
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * width]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _ocr_page(page: Any, number: int, height: float) -> tuple[list[_Line], str | None]:
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return [], "pages without a text layer were skipped: install the 'ocr' extra to OCR them"
    image = np.asarray(page.render(scale=200 / 72).to_pil().convert("RGB"))
    result, _ = RapidOCR()(image)
    scale = height / image.shape[0]
    lines = []
    for box, text, _score in sorted(result or [], key=lambda item: item[0][0][1]):
        top, bottom = box[0][1] * scale, box[2][1] * scale
        margin, edge = _bands(top, bottom, height)
        lines.append(_Line(text, bottom - top, top, bottom, number, margin, edge=edge))
    return lines, None


# Markdown and plain text


def parse_markdown(path: Path, config: ParsingConfig) -> ParsedDocument:
    blocks: list[Block] = []
    paragraph: list[str] = []
    table: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(Block("text", " ".join(paragraph), 1, 1))
            paragraph.clear()
        if table:
            if config.tables == "markdown":
                blocks.append(Block("table", "\n".join(table), 1, 1))
            table.clear()

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading:
            flush()
            blocks.append(Block("heading", heading.group(2).strip(), 1, 1, min(3, len(heading.group(1)))))
        elif line.startswith("|"):
            if paragraph:
                blocks.append(Block("text", " ".join(paragraph), 1, 1))
                paragraph.clear()
            table.append(line)
        elif not line:
            flush()
        else:
            if table:
                flush()
            paragraph.append(line)
    flush()
    title = next((b.text for b in blocks if b.kind == "heading"), path.stem)
    return ParsedDocument(title, blocks, table_count=sum(b.kind == "table" for b in blocks))


def parse_text(path: Path, config: ParsingConfig) -> ParsedDocument:
    text = path.read_text(encoding="utf-8", errors="replace")
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    return ParsedDocument(path.stem, [Block("text", p, 1, 1) for p in paragraphs])


PARSERS: dict[str, Callable[[Path, ParsingConfig], ParsedDocument]] = {
    ".pdf": parse_pdf,
    ".md": parse_markdown,
    ".txt": parse_text,
}
