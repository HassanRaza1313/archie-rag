"""The layout reader: Docling's page-layout and table-structure models, mapped onto Archie's blocks.

Docling (MIT) classifies each region of a page (title, heading, paragraph, list, table, caption, page header,
footer) with a layout model, reconstructs tables with TableFormer, OCRs scanned pages, and reads DOCX, PPTX,
XLSX and HTML natively. It is optional (the ``layout`` extra) because its models add several hundred MB and
take seconds per page on CPU; ``engine: auto`` therefore only sends it the documents that need it.

Converters load their models once per process and are cached by the options that shape them.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import Any

from ragkit.config import ParsingConfig
from ragkit.parsing import Block, ParsedDocument

# Docling labels that are page furniture rather than content.
_FURNITURE = {"page_header", "page_footer"}
_HEADINGS = {"title": 1, "section_header": 2}


def layout_available() -> bool:
    return importlib.util.find_spec("docling") is not None


@lru_cache(maxsize=4)
def _converter(ocr: str, tables: bool) -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode, TableStructureOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(
        do_ocr=ocr != "off",
        do_table_structure=tables,
        table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE),
    )
    options.ocr_options.force_full_page_ocr = ocr == "force"
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


def parse_layout(path: Path, config: ParsingConfig) -> ParsedDocument:
    if not layout_available():
        raise ValueError("the layout reader needs the 'layout' extra: pip install 'ragkit[layout]'")
    result = _converter(config.ocr, config.tables == "markdown").convert(path)
    document = result.document
    blocks: list[Block] = []
    title = ""
    for item, _level in document.iterate_items():
        label = str(getattr(item, "label", "")).rsplit(".", 1)[-1].lower()
        page = item.prov[0].page_no if getattr(item, "prov", None) else 1
        if label in _FURNITURE and config.strip_headers_footers:
            continue
        if label == "table":
            if config.tables == "markdown" and (markdown := item.export_to_markdown(doc=document).strip()):
                blocks.append(Block("table", markdown, page, page))
            continue
        text = " ".join(str(getattr(item, "text", "") or "").split())
        if not text:
            continue
        if label in _HEADINGS:
            level = _HEADINGS[label] + max(0, int(getattr(item, "level", 1) or 1) - 1)
            blocks.append(Block("heading", text, page, page, min(3, level)))
            title = title or (text if label == "title" else "")
        else:
            blocks.append(Block("text", text, page, page))
    if path.suffix.lower() == ".pptx":
        blocks = _with_speaker_notes(path, blocks)
    title = title or next((b.text for b in blocks if b.kind == "heading"), path.stem)
    pages = max((b.page_end for b in blocks), default=1)
    return ParsedDocument(
        title,
        blocks,
        page_count=pages,
        table_count=sum(b.kind == "table" for b in blocks),
        warnings=[f"layout reader: {error.error_message}" for error in result.errors][:5],
        reader="layout",
    )


def _with_speaker_notes(path: Path, blocks: list[Block]) -> list[Block]:
    """Docling reads slide content but not speaker notes, which often hold the explanation; add them per slide."""
    from pptx import Presentation

    notes = {
        number: " ".join(slide.notes_slide.notes_text_frame.text.split())
        for number, slide in enumerate(Presentation(str(path)).slides, start=1)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip()
    }
    if not notes:
        return blocks
    merged: list[Block] = []
    for index, block in enumerate(blocks):
        merged.append(block)
        next_page = blocks[index + 1].page if index + 1 < len(blocks) else None
        if block.page in notes and next_page != block.page:
            merged.append(Block("text", f"Speaker notes: {notes.pop(block.page)}", block.page, block.page))
    merged.extend(Block("text", f"Speaker notes: {text}", page, page) for page, text in sorted(notes.items()))
    return merged
