"""Generate the ingestion benchmark corpus: synthetic, license-clean documents that stress parsing.

Every document is about the same fictional company, so retrieval must find the right passage, not just the
right file. Each one targets a failure mode of rule-based parsing:

    annual-report.pdf          two text columns, running header and page numbers
    pricing.pdf                a borderless table (aligned text, no ruling lines)
    research-paper.pdf         abstract, sections and a references list full of distractor numbers
    data-retention-rules.pdf   numbered clauses, the shape of legal and policy text
    lab-safety-scan.pdf        an image-only page (needs OCR)
    employee-policy.docx       Word headings, paragraphs and a table
    q3-review.pptx             slides with bullets and speaker notes
    inventory.xlsx             a spreadsheet; answers live in single rows
    support-faq.html           question-and-answer pairs in HTML

Usage: python bench/make_corpus.py [output_dir]   (default: data/sources/bench)
Needs the 'bench' extra (python-docx, python-pptx, openpyxl, pillow).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tests"))
from pdf_factory import PdfPage, write_pdf

COMPANY = "Northwind Labs"


def _paragraph(
    page: PdfPage, x: float, y: float, text: str, width: int, size: float = 10
) -> float:
    """Write wrapped text starting at y; return the y below it."""
    for line in textwrap.wrap(text, width):
        page.text(x, y, size, line)
        y -= size * 1.45
    return y - size * 0.8


def annual_report(path: Path) -> None:
    columns = [
        [
            (
                "Financial results",
                [
                    (
                        "Northwind Labs closed the year with revenue that grew 18 percent to 42.6 million dollars, "
                        "driven by the sensor platform and two large public-sector contracts in the second half."
                    ),
                    (
                        "Gross margin held at 61 percent despite higher component costs, because the team moved "
                        "assembly of the base station closer to its main suppliers."
                    ),
                ],
            ),
            (
                "Customers",
                [
                    (
                        "Customer churn fell to 4.1 percent over the year, the lowest level since the company was "
                        "founded, after the support team introduced quarterly health reviews."
                    ),
                ],
            ),
        ],
        [
            (
                "People and offices",
                [
                    (
                        "The Lisbon office opened in March with 35 engineers working on firmware and the data "
                        "pipeline. A second floor is planned for the following spring."
                    ),
                    "Total headcount reached 212 people across four countries by the end of December.",
                ],
            ),
            (
                "Research",
                [
                    (
                        "The board approved a 2.5 million dollar research fund dedicated to battery recycling, "
                        "to be spent over three years with two university partners."
                    ),
                ],
            ),
        ],
    ]

    def header(number: int) -> PdfPage:
        return (
            PdfPage()
            .text(72, 760, 8, f"{COMPANY} - Annual Report 2025")
            .text(300, 30, 8, str(number))
        )

    first = header(1).text(72, 720, 20, "Annual Report 2025", bold=True)
    laid_out = PdfPage()
    for x, sections in zip((72, 320), columns, strict=True):
        y = 680.0
        for heading, paragraphs in sections:
            laid_out.text(x, y, 13, heading, bold=True)
            y -= 22
            for body in paragraphs:
                y = _paragraph(laid_out, x, y, body, 46)
    # Draw row by row across both columns, as many PDF producers do: reading in drawing order then
    # interleaves the columns, which is the classic failure of text-layer parsers.
    first.texts.extend(sorted(laid_out.texts, key=lambda t: (-round(t[1]), t[0])))
    second = header(2).text(72, 720, 13, "Outlook", bold=True)
    _paragraph(
        second,
        72,
        698,
        "For the coming year the company expects revenue between 48 and 51 million "
        "dollars and plans to enter the Nordic market with a reseller partner.",
        46,
    )
    _paragraph(
        second,
        320,
        698,
        "Capital spending will rise to 3.8 million dollars, mostly for a new "
        "calibration lab in Porto that doubles sensor testing capacity.",
        46,
    )
    write_pdf(path, [first, second], title="Annual Report 2025")


def pricing(path: Path) -> None:
    page = PdfPage().text(72, 740, 18, "Pricing", bold=True)
    _paragraph(
        page,
        72,
        715,
        "All plans are billed monthly per workspace. Prices exclude VAT.",
        90,
    )
    rows = [
        ("Plan", "Monthly price", "Seats", "Support"),
        ("Starter", "$19", "3", "Email"),
        ("Team", "$49", "10", "Chat"),
        ("Business", "$129", "50", "Phone"),
        ("Enterprise", "Custom", "Unlimited", "Dedicated manager"),
    ]
    for index, row in enumerate(rows):
        y = 670 - index * 20
        for x, cell in zip((72, 190, 310, 400), row, strict=True):
            page.text(x, y, 10, cell, bold=index == 0)
    _paragraph(
        page,
        72,
        550,
        "Annual billing gives two months free on every plan except Enterprise. "
        "Seats above the plan limit cost 6 dollars each per month.",
        90,
    )
    write_pdf(path, [page], title="Pricing")


def research_paper(path: Path) -> None:
    first = PdfPage().text(
        72, 740, 16, "Closed-loop recovery of lithium from sensor batteries", bold=True
    )
    y = _paragraph(
        first, 72, 715, "M. Okafor, L. Ferreira - Northwind Labs Research", 90, 9
    )
    sections = [
        (
            "Abstract",
            (
                "We describe a low-temperature process that recovers lithium from spent sensor batteries "
                "without smelting. In pilot runs the method reached a recycling yield of 92.4 percent at a cost "
                "of 3.10 dollars per kilogram of cells."
            ),
        ),
        (
            "Method",
            (
                "Cells are discharged, shredded under nitrogen and leached in a citric acid solution at "
                "60 degrees Celsius for four hours. Lithium is precipitated as carbonate."
            ),
        ),
        (
            "Results",
            (
                "Across twelve pilot batches the median purity of the recovered lithium carbonate was "
                "99.1 percent, which meets the battery-grade threshold."
            ),
        ),
    ]
    for heading, body in sections:
        first.text(72, y, 12, heading, bold=True)
        y = _paragraph(first, 72, y - 18, body, 90)
    second = PdfPage().text(72, 740, 12, "References", bold=True)
    y = 718.0
    for ref in [
        "[1] Smith, J. et al. Pyrometallurgical recycling reaches a yield of 70 percent. J. Mater. 2019.",
        "[2] Chen, R. Hydrometallurgy of cathode powders at 85 degrees. Hydromet. Rev. 2021.",
        "[3] Patel, A. Cost models for battery recovery at 5 dollars per kilogram. Energy Policy 2022.",
    ]:
        y = _paragraph(second, 72, y, ref, 95, 9)
    write_pdf(
        path,
        [first, second],
        title="Closed-loop recovery of lithium from sensor batteries",
    )


def retention_rules(path: Path) -> None:
    page = PdfPage().text(72, 740, 16, "Data Retention Rules", bold=True)
    clauses = [
        (
            "1. Scope",
            "1.1 These rules apply to all customer data processed by Northwind Labs systems.",
        ),
        (
            "4. Backups",
            (
                "4.1 Backups are encrypted at rest. 4.2 Backups shall be retained for no longer than "
                "35 days, after which they are destroyed."
            ),
        ),
        (
            "7. Logs",
            (
                "7.1 Audit logs shall be kept for 400 days. 7.2 Debug logs shall be deleted after 14 days "
                "and must never contain customer passwords."
            ),
        ),
        (
            "9. Deletion requests",
            "9.1 Verified deletion requests are completed within 30 days of receipt.",
        ),
    ]
    y = 705.0
    for heading, body in clauses:
        page.text(72, y, 12, heading, bold=True)
        y = _paragraph(page, 72, y - 18, body, 90)
    write_pdf(path, [page], title="Data Retention Rules")


def lab_safety_scan(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1275, 1650), "white")
    draw = ImageDraw.Draw(image)
    lines = [
        ("Lab Safety Rules", 48),
        ("", 28),
        ("Eye protection is mandatory at all times in Lab 3.", 28),
        ("Fire extinguishers are inspected every six months.", 28),
        ("Report chemical spills immediately to extension 4417.", 28),
        ("Visitors must be accompanied by a trained staff member.", 28),
    ]
    y = 150
    for text, size in lines:
        draw.text((120, y), text, fill="black", font=ImageFont.load_default(size=size))
        y += int(size * 2.2)
    image.save(path, "PDF", resolution=150)


def employee_policy(path: Path) -> None:
    from docx import Document

    document = Document()
    document.add_heading("Employee Policy", 0)
    document.add_heading("Paid leave", 1)
    document.add_paragraph(
        "Employees accrue 2.25 days of paid leave per month of service. Unused leave of up "
        "to five days carries over into the next calendar year."
    )
    document.add_heading("Remote work", 1)
    document.add_paragraph(
        "Remote work is allowed up to three days per week with manager approval. "
        "Core collaboration hours are 10:00 to 15:00 Lisbon time."
    )
    document.add_heading("Travel expenses", 1)
    document.add_paragraph("The following limits apply to business travel:")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Expense", "Limit"
    for expense, limit in [
        ("Hotel per night", "180 EUR"),
        ("Meals per day", "60 EUR"),
        ("Taxi", "Receipt required"),
    ]:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = expense, limit
    document.save(path)


def q3_review(path: Path) -> None:
    from pptx import Presentation

    deck = Presentation()
    slides = [
        ("Q3 Business Review", ["Northwind Labs, October"], None),
        (
            "Q3 highlights",
            ["Signed 14 new enterprise customers", "Net promoter score rose to 61"],
            None,
        ),
        (
            "Delivery",
            ["Analytics module moved to Q1", "Firmware 5.2 shipped on time"],
            "The delay of the analytics module was caused by a vendor API change that broke data export.",
        ),
        (
            "Roadmap",
            ["Mobile app beta opens in November", "Self-serve billing in Q1"],
            None,
        ),
    ]
    for title, bullets, notes in slides:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.text = bullets[0]
        for bullet in bullets[1:]:
            body.add_paragraph().text = bullet
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
    deck.save(path)


def inventory(path: Path) -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Stock"
    sheet.append(["SKU", "Item", "Warehouse", "Quantity", "Reorder level"])
    items = [
        "Thermal sensor",
        "Humidity sensor",
        "Base station",
        "Battery pack",
        "Mounting kit",
        "Gateway",
    ]
    warehouses = ["Porto", "Lisbon", "Rotterdam", "Leeds", "Gdansk"]
    for i, item in enumerate(items):
        for j, warehouse in enumerate(warehouses):
            sku = f"NW-{100 + i * 10 + j}"
            quantity = 40 + (i * 37 + j * 23) % 400
            sheet.append([sku, item, warehouse, quantity, 50 + 10 * j])
    book.save(path)


def support_faq(path: Path) -> None:
    pairs = [
        (
            "How do I reset my password?",
            (
                "Open the sign-in page, choose Forgot password and follow the link we "
                "email you. Links expire after 20 minutes."
            ),
        ),
        (
            "Can I export my data to CSV?",
            (
                "Yes. Go to Settings, then Data, then Export. Exports are limited to one "
                "million rows per file."
            ),
        ),
        (
            "Which browsers are supported?",
            (
                "The dashboard supports the latest two versions of Chrome, Firefox, "
                "Safari and Edge."
            ),
        ),
        (
            "How do I add a sensor to a workspace?",
            (
                "Scan the QR code on the sensor with the mobile app, or enter its "
                "serial number under Devices, then Add device."
            ),
        ),
    ]
    body = "\n".join(f"  <section><h2>{q}</h2><p>{a}</p></section>" for q, a in pairs)
    path.write_text(
        f"<!doctype html>\n<html><head><title>Support FAQ</title></head>\n<body>\n<h1>Support FAQ</h1>\n"
        f"<nav><a href='/'>Home</a> | <a href='/docs'>Docs</a></nav>\n{body}\n</body></html>\n",
        encoding="utf-8",
    )


BUILDERS = {
    "annual-report.pdf": annual_report,
    "pricing.pdf": pricing,
    "research-paper.pdf": research_paper,
    "data-retention-rules.pdf": retention_rules,
    "lab-safety-scan.pdf": lab_safety_scan,
    "employee-policy.docx": employee_policy,
    "q3-review.pptx": q3_review,
    "inventory.xlsx": inventory,
    "support-faq.html": support_faq,
}


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, build in BUILDERS.items():
        build(out / name)
        print(f"wrote {out / name}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/sources/bench"))
