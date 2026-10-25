"""Write small, valid PDFs for parser tests (standard Helvetica fonts, optional ruled tables)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

FONTS = {"regular": "F1", "bold": "F2"}


@dataclass
class PdfPage:
    texts: list[tuple[float, float, float, str, str]] = field(default_factory=list)  # (x, y, size, text, font)
    lines: list[tuple[float, float, float, float]] = field(default_factory=list)  # (x0, y0, x1, y1)

    def text(self, x: float, y: float, size: float, value: str, *, bold: bool = False) -> PdfPage:
        self.texts.append((x, y, size, value, FONTS["bold" if bold else "regular"]))
        return self

    def grid(
        self,
        x0: float,
        y_top: float,
        column_widths: list[float],
        row_height: float,
        rows: list[list[str]],
        size: float = 10,
    ) -> PdfPage:
        width = sum(column_widths)
        for index in range(len(rows) + 1):
            y = y_top - index * row_height
            self.lines.append((x0, y, x0 + width, y))
        x = x0
        for column_width in [0.0, *column_widths]:
            x += column_width
            self.lines.append((x, y_top, x, y_top - len(rows) * row_height))
        for row_index, row in enumerate(rows):
            x = x0
            for cell, column_width in zip(row, column_widths, strict=True):
                self.text(x + 4, y_top - (row_index + 1) * row_height + 6, size, cell)
                x += column_width
        return self


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, pages: list[PdfPage], title: str | None = None) -> Path:
    objects: list[bytes] = []
    page_ids = [5 + 2 * i for i in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    for page_id, page in zip(page_ids, pages, strict=True):
        ops = [f"BT /{font} {size} Tf {x} {y} Td ({_escape(value)}) Tj ET" for x, y, size, value, font in page.texts]
        ops += [f"{x0} {y0} m {x1} {y1} l S" for x0, y0, x1, y1 in page.lines]
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    info_id = None
    if title:
        objects.append(f"<< /Title ({_escape(title)}) >>".encode())
        info_id = len(objects)
    body = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + content + b"\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    body += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    trailer = f"<< /Size {len(objects) + 1} /Root 1 0 R" + (f" /Info {info_id} 0 R" if info_id else "") + " >>"
    body += f"trailer\n{trailer}\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(body))
    return path
