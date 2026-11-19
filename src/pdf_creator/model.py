"""Data model shared by quotes (Angebote) and invoices (Rechnungen)."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

RECIPIENT = ("MusterAI GmbH", "Lange Str. 2", "96052 Bamberg")

VAT_RATE = Decimal("0.19")
SKONTO_RATE = Decimal("0.02")

type Kind = Literal["angebot", "rechnung"]


def round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Sender:
    company: str
    legal_form: Literal["einzel", "gmbh"]
    owner: str  # Inhaber or Geschäftsführer, depending on legal form
    contact: str
    street: str
    postal_code: str
    city: str
    phone: str
    email: str
    website: str
    tax_number: str
    vat_id: str
    register: str | None  # e.g. "Amtsgericht Bamberg, HRB 00123" (GmbH only)
    bank_name: str
    iban: str
    bic: str
    hourly_rate: Decimal  # journeyman rate (Geselle)


@dataclass(frozen=True)
class LineItem:
    description: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    group: str

    @property
    def total(self) -> Decimal:
        return round_money(self.quantity * self.unit_price)


@dataclass(frozen=True)
class Document:
    kind: Kind
    number: str
    issue_date: date
    customer_no: str
    sender: Sender
    subject: str  # Bauvorhaben
    location: str  # room / area at the recipient address (Leistungsort)
    items: list[LineItem]
    # quote only
    valid_until: date | None = None
    # invoice only
    performance_start: date | None = None  # set when the service spans a period
    performance_end: date | None = None
    due_date: date | None = None
    skonto_days: int | None = None
    # invoice of a quote/invoice pair only
    quote_number: str | None = None
    quote_date: date | None = None

    @property
    def net(self) -> Decimal:
        return sum((item.total for item in self.items), Decimal("0.00"))

    @property
    def vat(self) -> Decimal:
        return round_money(self.net * VAT_RATE)

    @property
    def gross(self) -> Decimal:
        return self.net + self.vat

    @property
    def skonto_amount(self) -> Decimal | None:
        return round_money(self.gross * SKONTO_RATE) if self.skonto_days else None

    @property
    def skonto_date(self) -> date | None:
        return self.issue_date + timedelta(days=self.skonto_days) if self.skonto_days else None
