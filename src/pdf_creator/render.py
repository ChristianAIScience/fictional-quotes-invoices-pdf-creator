"""Render a Document as a DIN 5008 style A4 PDF with sample marking on every page."""

from datetime import date
from decimal import Decimal
from functools import partial
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import getAscentDescent, stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageTemplate, Paragraph, Spacer, Table,
    TableStyle,
)

from pdf_creator.model import RECIPIENT, VAT_RATE, Document, Sender

FOOTER_NOTE = "Fiktives Musterdokument – keine echte Rechnung / kein echtes Angebot"
WATERMARK = "MUSTER"

PAGE_W, PAGE_H = A4
LEFT, RIGHT = 25 * mm, 20 * mm
CONTENT_W = PAGE_W - LEFT - RIGHT
BODY_BOTTOM = 40 * mm
FIRST_BODY_TOP = PAGE_H - 98 * mm  # DIN 5008: text starts below the address/info zone
LATER_BODY_TOP = PAGE_H - 20 * mm
FOOTER_COLUMNS_X = (LEFT, LEFT + 62 * mm, LEFT + 112 * mm)
FOOTER_FONT = ("Helvetica", 7)
LETTERHEAD_FONT = ("Helvetica-Bold", 18)
LETTERHEAD_Y = 22 * mm  # baselines measured from the top of the page
TAGLINE_Y = 28 * mm
RIGHT_BLOCK_Y = tuple((17 + 4 * n) * mm for n in range(4))
TAGLINE = "Möbelbau · Innenausbau · Reparaturen"
SMALL_FONT = ("Helvetica", 9)  # tagline and right-aligned address block
RETURN_FONT = ("Helvetica", 7)
INFO_FONT = ("Helvetica", 8.5)
INFO_LABEL_X, INFO_VALUE_X = 125 * mm, 155 * mm
GAP = 2 * mm
ITEM_COL_WIDTHS = (10 * mm, 86 * mm, 14 * mm, 16 * mm, 20 * mm, 19 * mm)
CELL_PADDING = 6  # reportlab's default left/right cell padding in pt

TEXT = ParagraphStyle("text", fontName="Helvetica", fontSize=9.5, leading=13)
CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=10.5)
TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=14, leading=18, spaceAfter=4)
GREY = colors.Color(0.92, 0.92, 0.92)


def format_money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def format_quantity(value: Decimal) -> str:
    return f"{value.normalize():f}".replace(".", ",")


def format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def fits_footer(text: str, column: int) -> bool:
    """True if `text` fits footer column 0 (company), 1 (contact) or 2 (bank/tax) with a 2 mm gap."""
    end = FOOTER_COLUMNS_X[column + 1] if column + 1 < len(FOOTER_COLUMNS_X) else PAGE_W - RIGHT
    return stringWidth(text, *FOOTER_FONT) <= end - FOOTER_COLUMNS_X[column] - 2 * mm


def fits_footer_column(text: str) -> bool:
    """True if `text` fits the middle footer column (contact data) with a 2 mm gap."""
    return fits_footer(text, 1)


def _right_block_lines(s: Sender) -> list[str]:
    return [s.street, f"{s.postal_code} {s.city}", f"Tel. {s.phone}", s.email]


def _return_line(s: Sender) -> str:
    return f"{s.company} · {s.street} · {s.postal_code} {s.city}"


def _footer_columns(s: Sender) -> list[list[str]]:
    owner_label = "Geschäftsführer" if s.legal_form == "gmbh" else "Inhaber"
    return [
        [s.company, s.street, f"{s.postal_code} {s.city}", f"{owner_label}: {s.owner}", s.register or ""],
        [f"Tel. {s.phone}", s.email, s.website],
        [s.bank_name, f"IBAN {s.iban}", f"BIC {s.bic}", f"Steuernr. {s.tax_number}", f"USt-IdNr. {s.vat_id}"],
    ]


def _site_line(location: str) -> str:
    return f"MusterAI GmbH, {location}, {RECIPIENT[1]}, {RECIPIENT[2]}"


def drawable(text: str) -> bool:
    """True if the standard PDF fonts (WinAnsi encoding) can draw every character of `text`."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return text.isprintable()


def _same_height(font_a: tuple, y_a: float, font_b: tuple, y_b: float) -> bool:
    """True if two text lines (baselines measured from the top) share some vertical space."""
    def band(font, y):
        ascent, descent = getAscentDescent(*font)
        return y - ascent, y - descent  # descent is negative
    (top_a, bottom_a), (top_b, bottom_b) = band(font_a, y_a), band(font_b, y_b)
    return top_a < bottom_b and top_b < bottom_a


def sender_layout_problems(s: Sender) -> list[str]:
    """Every sender line that would not fit (or not be drawable) where _decorate draws it."""
    problems = []
    texts = [s.company, s.owner, s.contact, s.street, s.postal_code, s.city, s.phone, s.email, s.website,
             s.register or "", s.bank_name, s.iban, s.bic, s.tax_number, s.vat_id]
    problems += [f"nicht darstellbare Zeichen: {t!r}" for t in texts if not drawable(t)]
    left = [(s.company, LETTERHEAD_FONT, LETTERHEAD_Y), (TAGLINE, SMALL_FONT, TAGLINE_Y)]
    right = [(line, SMALL_FONT, y) for line, y in zip(_right_block_lines(s), RIGHT_BLOCK_Y)]
    for (l_text, l_font, l_y), (r_text, r_font, r_y) in ((l, r) for l in left for r in right):
        if _same_height(l_font, l_y, r_font, r_y) and (
                stringWidth(l_text, *l_font) + GAP + stringWidth(r_text, *r_font) > CONTENT_W):
            problems.append(f"Briefkopf: {l_text!r} überlappt {r_text!r}")
    if stringWidth(_return_line(s), *RETURN_FONT) > INFO_LABEL_X - LEFT - GAP:
        problems.append(f"Absenderzeile zu lang: {_return_line(s)!r}")
    for value in (s.contact, s.phone):
        if stringWidth(value, *INFO_FONT) > PAGE_W - RIGHT - INFO_VALUE_X:
            problems.append(f"Infoblock zu schmal für {value!r}")
    for column, lines in enumerate(_footer_columns(s)):
        problems += [f"Fußzeile zu schmal für {line!r}" for line in lines if not fits_footer(line, column)]
    return problems


def fits_amount(value: Decimal) -> bool:
    """True if a money amount fits the price/total columns of the items table."""
    return stringWidth(format_money(value), CELL.fontName, CELL.fontSize) <= ITEM_COL_WIDTHS[5] - 2 * CELL_PADDING


def job_layout_problems(subject: str, location: str, descriptions: list[str]) -> list[str]:
    """Job texts that would wrap (Bauvorhaben, Leistungsort, item lines) or not be drawable."""
    problems = [f"nicht darstellbare Zeichen: {t!r}" for t in (subject, location, *descriptions) if not drawable(t)]
    label_width = lambda label: stringWidth(label, "Helvetica-Bold", TEXT.fontSize)
    for label, text in (("Bauvorhaben: ", subject), ("Leistungsort: ", _site_line(location))):
        if label_width(label) + stringWidth(text, TEXT.fontName, TEXT.fontSize) > CONTENT_W:
            problems.append(f"{label.rstrip(': ')} länger als eine Zeile: {text!r}")
    problems += [f"Beschreibung länger als eine Zeile: {d!r}" for d in descriptions if not fits_description_line(d)]
    return problems


def fits_description_line(text: str) -> bool:
    """True if an item description fits on one line of the items table."""
    return stringWidth(text, CELL.fontName, CELL.fontSize) <= ITEM_COL_WIDTHS[1] - 2 * CELL_PADDING


def filename(doc: Document) -> str:
    name = doc.sender.company
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        name = name.replace(a, b)
    slug = "-".join("".join(c if c.isalnum() else " " for c in name).split())
    return f"{doc.number}_{slug}.pdf"


def _info_rows(doc: Document) -> list[tuple[str, str]]:
    s = doc.sender
    if doc.kind == "angebot":
        return [
            ("Angebots-Nr.", doc.number),
            ("Datum", format_date(doc.issue_date)),
            ("Kunden-Nr.", doc.customer_no),
            ("Gültig bis", format_date(doc.valid_until)),
            ("Ansprechpartner", s.contact),
            ("Telefon", s.phone),
        ]
    if doc.performance_start:
        performance = ("Leistungszeitraum", f"{format_date(doc.performance_start)} – {format_date(doc.performance_end)}")
    else:
        performance = ("Leistungsdatum", format_date(doc.performance_end))
    quote_ref = (
        [("Angebots-Nr.", doc.quote_number), ("Angebotsdatum", format_date(doc.quote_date))]
        if doc.quote_number else []
    )
    return [
        ("Rechnungs-Nr.", doc.number),
        ("Rechnungsdatum", format_date(doc.issue_date)),
        ("Kunden-Nr.", doc.customer_no),
        *quote_ref,
        performance,
        ("Ansprechpartner", s.contact),
        ("Telefon", s.phone),
    ]


def _decorate(canvas: Canvas, _template: BaseDocTemplate, doc: Document, first: bool) -> None:
    s = doc.sender
    canvas.saveState()
    if first:
        # letterhead
        canvas.setFont(*LETTERHEAD_FONT)
        canvas.drawString(LEFT, PAGE_H - LETTERHEAD_Y, s.company)
        canvas.setFont(*SMALL_FONT)
        canvas.setFillColor(colors.dimgrey)
        canvas.drawString(LEFT, PAGE_H - TAGLINE_Y, TAGLINE)
        for line, y in zip(_right_block_lines(s), RIGHT_BLOCK_Y):
            canvas.drawRightString(PAGE_W - RIGHT, PAGE_H - y, line)
        canvas.setFillColor(colors.black)
        # return address line and recipient (window envelope field)
        canvas.setFont(*RETURN_FONT)
        canvas.drawString(LEFT, PAGE_H - 57 * mm, _return_line(s))
        canvas.setFont("Helvetica", 10)
        for n, line in enumerate(RECIPIENT):
            canvas.drawString(LEFT, PAGE_H - (66 + 5 * n) * mm, line)
        # info block
        canvas.setFont(*INFO_FONT)
        for n, (label, value) in enumerate(_info_rows(doc)):
            y = PAGE_H - (52 + 4.5 * n) * mm
            canvas.drawString(INFO_LABEL_X, y, label)
            canvas.drawString(INFO_VALUE_X, y, value)
    # sample note, page number, company footer
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(colors.darkred)
    canvas.drawString(LEFT, 36 * mm, FOOTER_NOTE)
    canvas.setFillColor(colors.black)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(PAGE_W - RIGHT, 36 * mm, f"Seite {canvas.getPageNumber()}")
    canvas.setLineWidth(0.4)
    canvas.line(LEFT, 33 * mm, PAGE_W - RIGHT, 33 * mm)
    canvas.setFont(*FOOTER_FONT)
    for x, lines in zip(FOOTER_COLUMNS_X, _footer_columns(s)):
        for n, line in enumerate(lines):
            canvas.drawString(x, (29 - 3.3 * n) * mm, line)
    # watermark on top of the content, semi-transparent
    canvas.setFillColor(colors.Color(0.5, 0.5, 0.5, alpha=0.18))
    canvas.setFont("Helvetica-Bold", 130)
    canvas.translate(PAGE_W / 2, PAGE_H / 2)
    canvas.rotate(45)
    canvas.drawCentredString(0, -45, WATERMARK)
    canvas.restoreState()


def _items_table(doc: Document) -> Table:
    rows: list[list] = [["Pos.", "Beschreibung", "Menge", "Einheit", "Einzelpreis €", "Gesamt €"]]
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), GREY),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
        ("TOPPADDING", (0, 1), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 1.5),
    ]
    for pos, item in enumerate(doc.items, 1):
        rows.append([
            str(pos),
            Paragraph(escape(item.description), CELL),
            format_quantity(item.quantity),
            item.unit,
            format_money(item.unit_price),
            format_money(item.total),
        ])
    return Table(rows, colWidths=list(ITEM_COL_WIDTHS), repeatRows=1, style=TableStyle(style))


def _totals_table(doc: Document) -> Table:
    vat_percent = format_quantity(VAT_RATE * 100)
    rows = [
        ["Nettobetrag", f"{format_money(doc.net)} €"],
        [f"zzgl. {vat_percent} % USt.", f"{format_money(doc.vat)} €"],
        ["Gesamtbetrag", f"{format_money(doc.gross)} €"],
    ]
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]
    return Table(rows, colWidths=[50 * mm, 30 * mm], hAlign="RIGHT", style=TableStyle(style))


def _closing(doc: Document) -> list[str]:
    if doc.kind == "angebot":
        return [
            f"Dieses Angebot ist gültig bis zum {format_date(doc.valid_until)}. Die Ausführung erfolgt nach "
            "Absprache; Mehr- oder Minderleistungen werden nach tatsächlichem Aufwand abgerechnet.",
            "Wir würden uns über Ihren Auftrag freuen.",
        ]
    lines = [f"Zahlbar innerhalb von 14 Tagen ohne Abzug bis zum {format_date(doc.due_date)}."]
    if doc.skonto_days:
        lines.append(
            f"Bei Zahlung bis zum {format_date(doc.skonto_date)} gewähren wir 2 % Skonto "
            f"({format_money(doc.skonto_amount)} €); Zahlbetrag dann {format_money(doc.gross - doc.skonto_amount)} €."
        )
    lines.append("Bitte geben Sie bei der Zahlung die Rechnungsnummer an. Vielen Dank für Ihren Auftrag.")
    return lines


def render(doc: Document, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename(doc)
    title = "Angebot" if doc.kind == "angebot" else "Rechnung"
    template = BaseDocTemplate(
        str(path), pagesize=A4, invariant=True,
        title=f"{title} {doc.number}", author=doc.sender.company, subject=FOOTER_NOTE,
    )
    frame = partial(Frame, LEFT, BODY_BOTTOM, CONTENT_W, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    template.addPageTemplates([
        PageTemplate("first", [frame(FIRST_BODY_TOP - BODY_BOTTOM)], onPageEnd=partial(_decorate, doc=doc, first=True)),
        PageTemplate("later", [frame(LATER_BODY_TOP - BODY_BOTTOM)], onPageEnd=partial(_decorate, doc=doc, first=False)),
    ])
    intro = (
        "vielen Dank für Ihre Anfrage. Gerne bieten wir Ihnen die folgenden Leistungen an:"
        if doc.kind == "angebot"
        else "für die ausgeführten Arbeiten erlauben wir uns, wie folgt abzurechnen:"
    )
    site = _site_line(doc.location)
    story = [
        NextPageTemplate("later"),
        Paragraph(f"{title} Nr. {doc.number}", TITLE),
        Paragraph(f"<b>Bauvorhaben:</b> {escape(doc.subject)}", TEXT),
        Paragraph(f"<b>Leistungsort:</b> {escape(site)}", TEXT),
        Spacer(0, 3 * mm),
        Paragraph("Sehr geehrte Damen und Herren,", TEXT),
        Spacer(0, 2 * mm),
        Paragraph(intro, TEXT),
        Spacer(0, 2 * mm),
        _items_table(doc),
        Spacer(0, 2 * mm),
        KeepTogether([_totals_table(doc)]),
        Spacer(0, 3 * mm),
        *(Paragraph(escape(line), TEXT) for line in _closing(doc)),
    ]
    template.build(story)
    return path
