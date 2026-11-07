"""Chunking strategies.

``structure`` packs whole paragraphs within a section up to ``chunk_size`` tokens, crosses page breaks but
not section breaks (tiny sections merge forward), overlaps by whole trailing sentences, and keeps tables as
their own chunks with the header row repeated when split. ``fixed`` slides equal-size windows over the text.

The other strategies are templates over the same chunker, for document shapes where those defaults hurt:
``sections`` keeps every section on its own (FAQs, numbered clauses), ``slides`` makes one chunk per slide or
page, and ``rows`` splits tables into small groups of rows with the header repeated (spreadsheets). ``auto``
picks a template per document from its file type and headings, and drops the references list of papers.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, replace

from ragkit.config import ChunkingConfig
from ragkit.parsing import Block, ParsedDocument
from ragkit.textutil import estimate_tokens, estimate_tokens_for_length, split_sentences


@dataclass
class Chunk:
    ordinal: int
    text: str
    embed_text: str
    section: str
    page_start: int
    page_end: int
    content_type: str
    tokens: int
    template: str = "structure"
    excluded: bool = False


@dataclass
class _Unit:
    text: str
    section: str
    page_start: int
    page_end: int
    is_overlap: bool = False


@dataclass(frozen=True)
class Template:
    merge_small_sections: bool = True
    split_on_page: bool = False
    table_rows_share: int = 1  # tables split into parts of at most chunk_size // this
    drop_sections: tuple[str, ...] = ()


TEMPLATES: dict[str, Template] = {
    "structure": Template(),
    "sections": Template(merge_small_sections=False),
    "slides": Template(merge_small_sections=False, split_on_page=True),
    "rows": Template(table_rows_share=8),
}
STRATEGIES = ("auto", *TEMPLATES, "fixed")
_REFERENCES = ("references", "bibliography", "works cited")
_QUESTION_OR_CLAUSE = re.compile(r"\?\s*$|^\s*(\d+(\.\d+)*|[A-Z]|[IVX]+)[.)]\s")


def choose_template(document: ParsedDocument) -> tuple[str, Template]:
    """The template ``auto`` uses for a document, from its file type and the shape of its headings."""
    if document.file_type in (".xlsx", ".csv"):
        return "rows", TEMPLATES["rows"]
    if document.file_type == ".pptx":
        return "slides", TEMPLATES["slides"]
    headings = [b.text for b in document.blocks if b.kind == "heading" and b.level > 1]
    if len(headings) >= 3 and sum(bool(_QUESTION_OR_CLAUSE.search(h)) for h in headings) >= 0.6 * len(headings):
        return "sections", TEMPLATES["sections"]
    names = {h.lower().strip(" :") for h in headings}
    if "abstract" in names and names & set(_REFERENCES):
        return "paper", replace(TEMPLATES["structure"], drop_sections=_REFERENCES)
    return "structure", TEMPLATES["structure"]


def chunk_document(document: ParsedDocument, config: ChunkingConfig) -> list[Chunk]:
    if config.strategy == "fixed":
        template_name, pieces = "fixed", fixed_chunks(document, config)
    else:
        template_name, template = (
            choose_template(document) if config.strategy == "auto" else (config.strategy, TEMPLATES[config.strategy])
        )
        pieces = structure_chunks(document, config, template)
    return [
        Chunk(
            ordinal=index,
            text=text,
            embed_text=embed_text_for(document.title, section, text, config.section_headers),
            section=section,
            page_start=start,
            page_end=end,
            content_type=kind,
            tokens=estimate_tokens(text),
            template=template_name,
        )
        for index, (text, section, start, end, kind) in enumerate(pieces)
    ]


Piece = tuple[str, str, int, int, str]  # text, section path, page start, page end, content type


def embed_text_for(title: str, section: str, text: str, with_headers: bool) -> str:
    if not with_headers:
        return text
    path = f"{title} › {section}" if section and section != title else title
    return f"{path}\n\n{text}"


def structure_chunks(
    document: ParsedDocument, config: ChunkingConfig, template: Template = TEMPLATES["structure"]
) -> list[Piece]:
    size = config.chunk_size
    overlap_budget = size * config.overlap_pct // 100
    min_tokens = size // 4 if template.merge_small_sections else 0
    dropping = False
    pieces: list[Piece] = []
    current: list[_Unit] = []
    headings: list[tuple[int, str]] = []

    def tokens_of(units: list[_Unit]) -> int:
        return sum(estimate_tokens(u.text) for u in units)

    def flush(carry_overlap: bool) -> None:
        if not current or all(u.is_overlap for u in current):
            current.clear()
            return
        text = " ".join(u.text for u in current)
        pieces.append((text, current[0].section, current[0].page_start, current[-1].page_end, "text"))
        tail = _overlap_tail(text, overlap_budget) if carry_overlap else ""
        last = current[-1]
        current.clear()
        if tail:
            current.append(_Unit(tail, last.section, last.page_end, last.page_end, is_overlap=True))

    for block in document.blocks:
        if block.kind == "heading":
            while headings and headings[-1][0] >= block.level:
                headings.pop()
            headings.append((block.level, block.text))
            dropping = block.text.lower().strip(" :") in template.drop_sections
            continue
        if dropping:
            continue
        if template.split_on_page and current and current[-1].page_end != block.page:
            flush(carry_overlap=False)
        section = " › ".join(text for _, text in headings)
        if block.kind == "table":
            flush(carry_overlap=False)
            parts = _split_table(block, max(32, size // template.table_rows_share))
            pieces.extend((part, section, block.page, block.page_end, "table") for part in parts)
            continue
        for unit in _units(block, section, size):
            section_changed = bool(current) and current[-1].section != unit.section
            if section_changed and tokens_of(current) >= min_tokens:
                flush(carry_overlap=False)
            elif current and tokens_of(current) + estimate_tokens(unit.text) > size:
                flush(carry_overlap=True)
            current.append(unit)
    flush(carry_overlap=False)
    return pieces


def _units(block: Block, section: str, size: int) -> list[_Unit]:
    if estimate_tokens(block.text) <= size:
        return [_Unit(block.text, section, block.page, block.page_end)]
    units = []
    for sentence in split_sentences(block.text):
        for part in _hard_split(sentence, size):
            units.append(_Unit(part, section, block.page, block.page_end))
    return units


def _hard_split(text: str, size: int) -> list[str]:
    """Split text with no usable sentence boundaries into word windows of at most ``size`` tokens."""
    if estimate_tokens(text) <= size:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    length = 0
    for token in text.split():
        if current and estimate_tokens_for_length(length + len(token)) > size:
            parts.append(" ".join(current))
            current, length = [], 0
        current.append(token)
        length += len(token) + 1
    if current:
        parts.append(" ".join(current))
    return parts


def _overlap_tail(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    tail: list[str] = []
    for sentence in reversed(split_sentences(text)):
        if estimate_tokens(" ".join([sentence, *tail])) > budget:
            break
        tail.insert(0, sentence)
    return " ".join(tail)


def _split_table(block: Block, size: int) -> list[str]:
    lines = block.text.splitlines()
    if estimate_tokens(block.text) <= size or len(lines) <= 3:
        return [block.text]
    header, rows = lines[:2], lines[2:]
    parts, current = [], list(header)
    for row in rows:
        if len(current) > 2 and estimate_tokens("\n".join([*current, row])) > size:
            parts.append("\n".join(current))
            current = list(header)
        current.append(row)
    parts.append("\n".join(current))
    return parts


def fixed_chunks(document: ParsedDocument, config: ChunkingConfig) -> list[Piece]:
    texts, page_offsets, pages = [], [], []
    offset = 0
    for block in document.blocks:
        page_offsets.append(offset)
        pages.append(block.page)
        texts.append(block.text)
        offset += len(block.text) + 2
    full = "\n\n".join(texts)
    window = config.chunk_size * 4
    step = max(1, int(window * (1 - config.overlap_pct / 100)))
    pieces: list[Piece] = []
    start = 0
    while start < len(full):
        end = min(len(full), start + window)
        if end < len(full):
            end = full.rfind(" ", start + step // 2, end) if " " in full[start + step // 2 : end] else end
        text = full[start:end].strip()
        if text:
            first = pages[max(0, bisect_right(page_offsets, start) - 1)]
            last = pages[max(0, bisect_right(page_offsets, end - 1) - 1)]
            pieces.append((text, "", first, last, "text"))
        if end >= len(full):
            break
        next_start = start + step
        space = full.find(" ", next_start)
        start = space + 1 if 0 <= space < end else next_start
    return pieces
