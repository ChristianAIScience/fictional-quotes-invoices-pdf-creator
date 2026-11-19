from datetime import date
from decimal import Decimal

from pdf_creator.model import RECIPIENT, Document, LineItem, Sender, round_money

D = Decimal


def make_sender() -> Sender:
    return Sender(
        company="Schreinerei Spanmeier",
        legal_form="einzel",
        owner="Anna Spanmeier",
        contact="Anna Spanmeier",
        street="Gewerbering 4",
        postal_code="96103",
        city="Hallstadt",
        phone="030 23125 123",
        email="info@schreinerei-spanmeier.example.de",
        website="www.schreinerei-spanmeier.example.de",
        tax_number="000/123/45678",
        vat_id="DE000123456",
        register=None,
        bank_name="Musterbank Franken eG",
        iban="DE00 1234 5678 9012 3456 78",
        bic="MUSTDEM0XXX",
        hourly_rate=D("62.00"),
    )


def make_document(items: list[LineItem]) -> Document:
    return Document(
        kind="rechnung",
        number="RE-2026-0001",
        issue_date=date(2026, 3, 10),
        customer_no="K-12345",
        sender=make_sender(),
        subject="Reparaturarbeiten",
        location="Büros 1. OG",
        items=items,
        performance_start=None,
        performance_end=date(2026, 3, 5),
        due_date=date(2026, 3, 24),
        skonto_days=7,
    )


def test_round_money_half_up():
    assert round_money(D("15.4375")) == D("15.44")
    assert round_money(D("0.005")) == D("0.01")
    assert round_money(D("2.004")) == D("2.00")


def test_line_total_is_rounded_once():
    item = LineItem("Kantenband", D("1.25"), "lfm", D("12.35"), "Material")
    assert item.total == D("15.44")


def test_totals_sum_rounded_line_totals_then_round_vat():
    # each line 0.335 * 1 -> raw sum would be 1.005; rounded lines are 0.34 each
    items = [
        LineItem("A", D("0.5"), "Stk.", D("0.67"), "Material"),
        LineItem("B", D("0.5"), "Stk.", D("0.67"), "Material"),
    ]
    doc = make_document(items)
    assert doc.net == D("0.68")
    assert doc.vat == D("0.13")  # 0.68 * 0.19 = 0.1292
    assert doc.gross == D("0.81")


def test_realistic_totals():
    items = [
        LineItem("Arbeitsleistung", D("3.5"), "Std.", D("62.00"), "Arbeitsleistung"),
        LineItem("Scharniere", D("6"), "Stk.", D("7.85"), "Material"),
        LineItem("Anfahrtspauschale", D("1"), "Pauschale", D("45.00"), "Sonstiges"),
    ]
    doc = make_document(items)
    assert doc.net == D("309.10")  # 217.00 + 47.10 + 45.00
    assert doc.vat == D("58.73")  # 58.729
    assert doc.gross == D("367.83")
    assert doc.skonto_amount == D("7.36")  # 2 % of 367.83 = 7.3566
    assert doc.skonto_date == date(2026, 3, 17)


def test_recipient_is_fixed():
    assert RECIPIENT == ("MusterAI GmbH", "Lange Str. 2", "96052 Bamberg")
