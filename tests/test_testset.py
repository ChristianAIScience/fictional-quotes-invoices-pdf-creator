import json
from collections import Counter
from datetime import date
from decimal import Decimal

import pytest
from pypdf import PdfReader

from pdf_creator.generator import generate_pairs
from pdf_creator.model import RECIPIENT
from pdf_creator.render import FOOTER_NOTE, format_money
from pdf_creator.testset import DEVIATION_TYPES, build_cases, write_testset

TODAY = date(2026, 11, 15)


@pytest.fixture(scope="module")
def cases():
    return build_cases(seed=42, count=40, today=TODAY)


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    out = tmp_path_factory.mktemp("testset")
    cases = build_cases(seed=7, count=10, today=TODAY)
    write_testset(cases, out)
    return cases, out


def test_same_seed_same_cases(cases):
    assert build_cases(seed=42, count=40, today=TODAY) == cases


def test_pair_ids_sequential(cases):
    assert [c.pair_id for c in cases] == [f"{n:04d}" for n in range(1, 41)]


def test_thirty_percent_deviate_with_balanced_types(cases):
    deviating = [c for c in cases if c.deviations]
    assert len(deviating) == 12
    assert all(len(c.deviations) == 1 for c in deviating)
    assert Counter(c.deviations[0].type for c in deviating) == {t: 3 for t in DEVIATION_TYPES}


def test_clean_pairs_are_unchanged(cases):
    originals = generate_pairs(seed=42, count=40, today=TODAY)
    for case, (quote, invoice) in zip(cases, originals):
        if not case.deviations:
            assert (case.quote, case.invoice) == (quote, invoice)
            assert case.quote.items == case.invoice.items


def test_deviations_match_documents(cases):
    for case in (c for c in cases if c.deviations):
        d = case.deviations[0]
        q_items, i_items = case.quote.items, case.invoice.items
        match d.type:
            case "price_changed" | "quantity_changed":
                assert d.quote_position == d.invoice_position
                j = d.quote_position - 1
                q, i = q_items[j], i_items[j]
                assert q.description == i.description == d.description
                assert [x for n, x in enumerate(q_items) if n != j] == [x for n, x in enumerate(i_items) if n != j]
                if d.type == "price_changed":
                    assert q.quantity == i.quantity
                    assert i.unit_price > q.unit_price
                    assert (d.quote_value, d.invoice_value) == (f"{q.unit_price:.2f}", f"{i.unit_price:.2f}")
                else:
                    assert q.unit_price == i.unit_price and q.unit != "Pauschale"
                    assert i.quantity > q.quantity
                    assert (Decimal(d.quote_value), Decimal(d.invoice_value)) == (q.quantity, i.quantity)
            case "item_missing":
                assert d.invoice_position is None and d.quote_value is d.invoice_value is None
                j = d.quote_position - 1
                assert q_items[j].description == d.description
                assert q_items[:j] + q_items[j + 1:] == i_items
            case "item_added":
                assert d.quote_position is None and d.quote_value is d.invoice_value is None
                j = d.invoice_position - 1
                assert i_items[j].description == d.description
                assert i_items[:j] + i_items[j + 1:] == q_items
        # quote and invoice are each internally consistent; only the reference between them differs
        for doc in (case.quote, case.invoice):
            assert doc.net == sum(item.total for item in doc.items)


def test_writes_exactly_one_pdf_and_json_per_document(written):
    cases, out = written
    expected = {f"{kind}_{c.pair_id}.{ext}" for c in cases for kind in ("angebot", "rechnung") for ext in ("pdf", "json")}
    assert {p.name for p in out.iterdir()} == expected


def test_json_files_share_one_schema(written):
    _, out = written
    key_sets = {tuple(json.loads(p.read_text("utf-8"))) for p in out.glob("*.json")}
    assert len(key_sets) == 1


def test_json_values_match_documents(written):
    cases, out = written
    for case in cases:
        for doc, deviations in ((case.quote, None), (case.invoice, case.deviations)):
            data = json.loads((out / f"{doc.kind}_{case.pair_id}.json").read_text("utf-8"))
            assert data["file"] == f"{doc.kind}_{case.pair_id}.pdf"
            assert data["pair_id"] == case.pair_id
            assert data["doc_type"] == doc.kind
            assert data["number"] == doc.number
            assert data["issue_date"] == doc.issue_date.isoformat()
            assert data["recipient"] == list(RECIPIENT)
            assert data["sender"]["company"] == doc.sender.company
            assert data["sender"]["vat_id"] == doc.sender.vat_id
            assert data["sender"]["iban"] == doc.sender.iban
            assert [i["description"] for i in data["items"]] == [i.description for i in doc.items]
            assert [i["position"] for i in data["items"]] == list(range(1, len(doc.items) + 1))
            assert [Decimal(i["total"]) for i in data["items"]] == [i.total for i in doc.items]
            assert (data["net"], data["vat"], data["gross"]) == (f"{doc.net:.2f}", f"{doc.vat:.2f}", f"{doc.gross:.2f}")
            if doc.kind == "angebot":
                assert data["valid_until"] == doc.valid_until.isoformat()
                assert data["due_date"] is None and data["quote_number"] is None
                assert data["deviations"] is None
            else:
                assert data["valid_until"] is None
                assert data["due_date"] == doc.due_date.isoformat()
                assert data["quote_number"] == case.quote.number
                assert data["deviations"] == [
                    {
                        "type": d.type,
                        "quote_position": d.quote_position,
                        "invoice_position": d.invoice_position,
                        "description": d.description,
                        "quote_value": d.quote_value,
                        "invoice_value": d.invoice_value,
                    }
                    for d in deviations
                ]


def test_pdfs_show_ground_truth_values_on_one_marked_page(written):
    cases, out = written
    for case in cases:
        for doc in (case.quote, case.invoice):
            pages = PdfReader(out / f"{doc.kind}_{case.pair_id}.pdf").pages
            assert len(pages) == 1
            text = " ".join(pages[0].extract_text().split())  # long descriptions wrap onto two lines
            assert "MUSTER" in text and FOOTER_NOTE in text
            assert doc.number in text and format_money(doc.gross) in text
            for item in doc.items:
                assert item.description in text
                assert format_money(item.total) in text


def test_refuses_non_empty_output_dir(tmp_path):
    (tmp_path / "old.json").write_text("{}")
    with pytest.raises(FileExistsError):
        write_testset(build_cases(seed=1, count=1, today=TODAY), tmp_path)
