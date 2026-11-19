import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from pdf_creator.generator import generate, generate_pairs, load_data, make_domain
from pdf_creator.render import fits_footer_column

TODAY = date(2026, 11, 15)


@pytest.fixture(scope="module")
def docs():
    return generate(seed=42, count=20, kinds=("angebot", "rechnung"), today=TODAY)


def test_same_seed_same_documents(docs):
    assert generate(seed=42, count=20, kinds=("angebot", "rechnung"), today=TODAY) == docs


def test_different_seed_different_documents(docs):
    assert generate(seed=43, count=20, kinds=("angebot", "rechnung"), today=TODAY) != docs


def test_count_and_kinds(docs):
    assert len(docs) == 40
    assert sum(d.kind == "angebot" for d in docs) == 20
    assert all(d.kind == "rechnung" for d in generate(1, 3, ("rechnung",), TODAY))


def test_numbers_unique_and_formatted(docs):
    numbers = [d.number for d in docs]
    assert len(set(numbers)) == len(numbers)
    for d in docs:
        prefix = "AN" if d.kind == "angebot" else "RE"
        assert re.fullmatch(rf"{prefix}-{d.issue_date.year}-\d{{4}}", d.number)
        assert re.fullmatch(r"K-\d{5}", d.customer_no)


def test_dates(docs):
    for d in docs:
        assert TODAY - timedelta(days=365) < d.issue_date <= TODAY
        if d.kind == "angebot":
            assert d.valid_until == d.issue_date + timedelta(days=30)
            assert d.due_date is None
        else:
            assert d.valid_until is None
            assert d.due_date == d.issue_date + timedelta(days=14)
            assert d.performance_end is not None and d.performance_end < d.issue_date
            if d.performance_start:
                assert d.performance_start < d.performance_end
            assert d.skonto_days in (None, 7)


def test_sender_identifiers_are_fictional(docs):
    for s in (d.sender for d in docs):
        assert s.iban.startswith("DE00 ")
        assert re.fullmatch(r"DE00( \d{4}){4} \d{2}", s.iban)
        assert re.fullmatch(r"DE000\d{6}", s.vat_id)
        assert re.fullmatch(r"000/\d{3}/\d{5}", s.tax_number)
        assert s.email.endswith(".example.de")
        assert s.website.endswith(".example.de")
        assert re.fullmatch(r"(030 23125|069 90009|040 66969) \d{3}", s.phone)


def test_sender_town_and_legal_form_consistent(docs):
    towns = {t["city"]: t for t in load_data("senders")["towns"]}
    for s in (d.sender for d in docs):
        assert towns[s.city]["postal_code"] == s.postal_code
        if s.legal_form == "gmbh":
            assert s.register == f"Amtsgericht {towns[s.city]['court']}, HRB 00{s.register[-3:]}"
            assert "GmbH" in s.company
        else:
            assert s.register is None


def test_line_items(docs):
    for d in docs:
        groups = {i.group for i in d.items}
        assert {"Arbeitsleistung", "Material"} <= groups
        rate = d.sender.hourly_rate
        for i in d.items:
            assert i.quantity > 0 and i.unit_price > 0
            assert i.unit_price == i.unit_price.quantize(Decimal("0.01"))
            if i.group == "Arbeitsleistung":
                assert i.unit == "Std."
                assert i.unit_price == rate  # one hourly rate, no roles
                assert ":" not in i.description
            if i.group == "Sonstiges":
                assert i.unit == "Pauschale"


@pytest.fixture(scope="module")
def pairs():
    return generate_pairs(seed=5, count=20, today=TODAY)


def test_pairs_deterministic_and_unique(pairs):
    assert generate_pairs(seed=5, count=20, today=TODAY) == pairs
    numbers = [d.number for pair in pairs for d in pair]
    assert len(set(numbers)) == len(numbers) == 40


def test_pair_documents_match(pairs):
    for quote, invoice in pairs:
        assert (quote.kind, invoice.kind) == ("angebot", "rechnung")
        for field in ("sender", "customer_no", "subject", "location", "items"):
            assert getattr(invoice, field) == getattr(quote, field)
        assert invoice.gross == quote.gross
        assert (invoice.quote_number, invoice.quote_date) == (quote.number, quote.issue_date)
        assert quote.quote_number is None


def test_pair_dates_in_order(pairs):
    for quote, invoice in pairs:
        start = invoice.performance_start or invoice.performance_end
        assert quote.issue_date < start <= invoice.performance_end <= quote.valid_until
        assert invoice.performance_end < invoice.issue_date <= TODAY
        assert invoice.due_date == invoice.issue_date + timedelta(days=14)


def test_email_and_website_fit_pdf_footer_for_every_name():
    data = load_data("senders")
    for pattern in data["name_patterns"]:
        for surname in data["surnames"]:
            for first in data["first_names"]:
                company = pattern["pattern"].format(surname=surname, first=first)
                domain = make_domain(company, surname)
                assert fits_footer_column(f"info@{domain}"), domain
                assert fits_footer_column(f"www.{domain}"), domain


def test_long_domain_falls_back_to_surname_short_one_is_kept():
    assert make_domain("Tischlerei Furnierl & Söhne GmbH", "Furnierl") == "furnierl.example.de"
    assert make_domain("Schreinerei Spanmeier", "Spanmeier") == "schreinerei-spanmeier.example.de"
