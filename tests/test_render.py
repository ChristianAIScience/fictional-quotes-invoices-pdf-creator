from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from pypdf import PdfReader

from pdf_creator.generator import generate, generate_pairs, load_data
from pdf_creator.model import LineItem
from pdf_creator.render import FOOTER_NOTE, filename, format_money, render

TODAY = date(2026, 11, 15)


def page_texts(path) -> list[str]:
    return [page.extract_text() for page in PdfReader(path).pages]


@pytest.mark.parametrize("kind", ["angebot", "rechnung"])
def test_rendered_pdf_contains_required_content(tmp_path, kind):
    doc = generate(seed=7, count=1, kinds=(kind,), today=TODAY)[0]
    path = render(doc, tmp_path)
    assert path.name == filename(doc)
    texts = page_texts(path)
    full = "\n".join(texts)
    for page in texts:
        assert "MUSTER" in page
        assert FOOTER_NOTE in page
    for line in ("MusterAI GmbH", "Lange Str. 2", "96052 Bamberg"):
        assert line in full
    for value in (doc.number, doc.sender.company, doc.sender.vat_id, doc.sender.iban):
        assert value in full
    for amount in (doc.net, doc.vat, doc.gross):
        assert format_money(amount) in full


def test_long_document_marks_every_page(tmp_path):
    doc = generate(seed=7, count=1, kinds=("rechnung",), today=TODAY)[0]
    many = [LineItem(f"Position {n}", Decimal("1"), "Stk.", Decimal("9.99"), "Material") for n in range(80)]
    path = render(replace(doc, items=doc.items + many), tmp_path)
    texts = page_texts(path)
    assert len(texts) >= 3
    for n, page in enumerate(texts, 1):
        assert "MUSTER" in page
        assert FOOTER_NOTE in page
        assert f"Seite {n}" in page
        if n < len(texts):  # the last page may hold only the totals
            assert "Beschreibung" in page  # table header repeats


def test_items_listed_as_one_flat_list(tmp_path):
    doc = generate(seed=7, count=1, kinds=("rechnung",), today=TODAY)[0]
    full = "\n".join(page_texts(render(doc, tmp_path)))
    assert "Summe" not in full
    lines = full.splitlines()
    for group in ("Arbeitsleistung", "Material", "Sonstiges"):
        assert group not in lines  # no group heading rows
    for pos, item in enumerate(doc.items, 1):
        assert f"\n{pos}\n{item.description.split()[0]}" in full  # pypdf puts each cell on its own line


def test_generator_keeps_labor_material_extras_order():
    order = {"Arbeitsleistung": 0, "Material": 1, "Sonstiges": 2}
    for doc in generate(seed=3, count=10, kinds=("angebot", "rechnung"), today=TODAY):
        ranks = [order[i.group] for i in doc.items]
        assert ranks == sorted(ranks)


def test_generated_documents_fit_on_one_page_without_sign_off(tmp_path):
    for seed in range(15):
        for doc in generate(seed=seed, count=4, kinds=("angebot", "rechnung"), today=TODAY):
            texts = page_texts(render(doc, tmp_path))
            assert len(texts) == 1, doc.number
            assert "Mit freundlichen Grüßen" not in texts[0]


@pytest.mark.parametrize("kind", ["angebot", "rechnung"])
def test_worst_case_job_fits_on_one_page(tmp_path, kind):
    """Max item count with the longest descriptions, for every job template."""
    base = generate(seed=7, count=1, kinds=(kind,), today=TODAY)[0]
    if kind == "rechnung":
        base = replace(
            base, skonto_days=7, performance_start=base.performance_end,
            quote_number="AN-2026-0001", quote_date=base.issue_date,  # invoice of a pair has an extra line
        )
    data = load_data("jobs")
    for job in data["jobs"]:
        longest = lambda pool, n: sorted(pool, key=lambda e: len(e["description"]), reverse=True)[:n]
        items = [
            LineItem(e["description"], Decimal("10.5"), "Std.", Decimal("75.00"), "Arbeitsleistung")
            for e in longest(job["labor"], job["labor_count"][1])
        ] + [
            LineItem(e["description"], Decimal("12.5"), e["unit"], Decimal("999.99"), "Material")
            for e in longest(job["material"], job["material_count"][1])
        ] + [
            LineItem(e["description"], Decimal("1"), "Pauschale", Decimal("99.99"), "Sonstiges") for e in data["extras"]
        ]
        texts = page_texts(render(replace(base, items=items, subject=job["subject"]), tmp_path))
        assert len(texts) == 1, job["subject"]


def test_totals_never_split_across_pages(tmp_path):
    for doc in generate(seed=42, count=10, kinds=("angebot", "rechnung"), today=TODAY):
        texts = page_texts(render(doc, tmp_path))
        pages_with_totals = [n for n, t in enumerate(texts) if "Nettobetrag" in t or "Gesamtbetrag" in t]
        assert len(pages_with_totals) == 1, doc.number


def test_same_document_renders_identical_bytes(tmp_path):
    doc = generate(seed=7, count=1, kinds=("angebot",), today=TODAY)[0]
    a = render(doc, tmp_path / "a").read_bytes()
    b = render(doc, tmp_path / "b").read_bytes()
    assert a == b


def test_format_money():
    assert format_money(Decimal("1234567.5")) == "1.234.567,50"
    assert format_money(Decimal("0.13")) == "0,13"


def test_invoice_of_pair_references_quote(tmp_path):
    quote, invoice = generate_pairs(seed=5, count=1, today=TODAY)[0]
    quote_text = "\n".join(page_texts(render(quote, tmp_path)))
    texts = page_texts(render(invoice, tmp_path))
    assert len(texts) == 1
    for text in ("Angebots-Nr.", quote.number, "Angebotsdatum", f"{quote.issue_date:%d.%m.%Y}"):
        assert text in texts[0]
    assert "Angebotsdatum" not in quote_text
    assert format_money(invoice.gross) in quote_text and format_money(invoice.gross) in texts[0]
