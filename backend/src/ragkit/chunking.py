"""Chunking strategies.

``structure`` packs whole paragraphs within a section up to ``chunk_size`` tokens, crosses page breaks but
not section breaks (tiny sections merge forward), overlaps by whole trailing sentences, and keeps tables as
their own chunks with the header row repeated when split. ``fixed`` slides equal-size windows over the text.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass

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


@dataclass
class _Unit:
    text: str
    section: str
    page_start: int
    page_end: int
    is_overlap: bool = False


def chunk_document(document: ParsedDocument, config: ChunkingConfig) -> list[Chunk]:
    pieces = STRATEGIES[config.strategy](document, config)
    return [
        Chunk(
            ordinal=index,
            text=text,
            embed_text=_embed_text(document.title, section, text, config.section_headers),
            section=section,
            page_start=start,
            page_end=end,
            content_type=kind,
            tokens=estimate_tokens(text),
        )
        for index, (text, section, start, end, kind) in enumerate(pieces)
    ]


Piece = tuple[str, str, int, int, str]  # text, section path, page start, page end, content type


def _embed_text(title: str, section: str, text: str, with_headers: bool) -> str:
    if not with_headers:
        return text
    path = f"{title} › {section}" if section and section != title else title
    return f"{path}\n\n{text}"


def structure_chunks(document: ParsedDocument, config: ChunkingConfig) -> list[Piece]:
    size = config.chunk_size
    overlap_budget = size * config.overlap_pct // 100
    min_tokens = size // 4
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
            continue
        section = " › ".join(text for _, text in headings)
        if block.kind == "table":
            flush(carry_overlap=False)
            pieces.extend((part, section, block.page, block.page_end, "table") for part in _split_table(block, size))
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


STRATEGIES: dict[str, Callable[[ParsedDocument, ChunkingConfig], list[Piece]]] = {
    "structure": structure_chunks,
    "fixed": fixed_chunks,
}
