"""Seeded random generation of fictional joinery senders and documents."""

import random
import tomllib
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from importlib.resources import files

from pdf_creator.model import Document, Kind, LineItem, Sender
from pdf_creator.render import fits_footer_column

PREFIX = {"angebot": "AN", "rechnung": "RE"}


@cache
def load_data(name: str) -> dict:
    return tomllib.loads((files("pdf_creator") / "data" / f"{name}.toml").read_text("utf-8"))


def _slug(text: str) -> str:
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("&", "und")):
        text = text.lower().replace(a, b)
    return "-".join("".join(c if c.isalnum() else " " for c in text).split())


def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randrange(10)) for _ in range(n))


def _price(rng: random.Random, bounds: list[str]) -> Decimal:
    lo, hi = (int(Decimal(b) * 100) for b in bounds)
    return Decimal(rng.randint(lo, hi)) / 100


def _quantity(rng: random.Random, bounds: list[str], step: str = "1") -> Decimal:
    lo, hi, st = Decimal(bounds[0]), Decimal(bounds[1]), Decimal(step)
    return lo + st * rng.randint(0, int((hi - lo) / st))


def make_domain(company: str, surname: str) -> str:
    """Domain from the company name; only the surname if the email would not fit the PDF footer."""
    domain = f"{_slug(company.removesuffix(' GmbH'))}.example.de"
    return domain if fits_footer_column(f"info@{domain}") else f"{_slug(surname)}.example.de"


def make_sender(rng: random.Random) -> Sender:
    data = load_data("senders")
    surname, first = rng.choice(data["surnames"]), rng.choice(data["first_names"])
    pattern = rng.choice(data["name_patterns"])
    company = pattern["pattern"].format(surname=surname, first=first)
    town, bank = rng.choice(data["towns"]), rng.choice(data["banks"])
    domain = make_domain(company, surname)
    owner = f"{first} {surname}"
    is_gmbh = pattern["legal_form"] == "gmbh"
    iban = "DE00" + _digits(rng, 18)
    return Sender(
        company=company,
        legal_form=pattern["legal_form"],
        owner=owner,
        contact=owner if rng.random() < 0.5 else f"{rng.choice(data['first_names'])} {surname}",
        street=f"{rng.choice(data['streets'])} {rng.randint(1, 60)}",
        postal_code=town["postal_code"],
        city=town["city"],
        phone=f"{rng.choice(data['phone_blocks'])} {_digits(rng, 3)}",
        email=f"info@{domain}",
        website=f"www.{domain}",
        tax_number=f"000/{_digits(rng, 3)}/{_digits(rng, 5)}",
        vat_id="DE000" + _digits(rng, 6),
        register=f"Amtsgericht {town['court']}, HRB 00{_digits(rng, 3)}" if is_gmbh else None,
        bank_name=bank["name"],
        iban=" ".join(iban[i : i + 4] for i in range(0, len(iban), 4)),
        bic=bank["bic"],
        hourly_rate=Decimal(rng.randint(*(int(b) for b in data["hourly_rate"]))),
    )


def make_items(rng: random.Random, job: dict, hourly_rate: Decimal) -> list[LineItem]:
    items = []
    for labor in rng.sample(job["labor"], rng.randint(*job["labor_count"])):
        items.append(LineItem(
            labor["description"],
            _quantity(rng, labor["hours"], "0.5"),
            "Std.",
            hourly_rate.quantize(Decimal("0.01")),
            "Arbeitsleistung",
        ))
    for mat in rng.sample(job["material"], rng.randint(*job["material_count"])):
        items.append(LineItem(
            mat["description"],
            _quantity(rng, mat["qty"], mat.get("step", "1")),
            mat["unit"],
            _price(rng, mat["price"]),
            "Material",
        ))
    for extra in load_data("jobs")["extras"]:
        if rng.random() < extra["probability"]:
            items.append(LineItem(
                extra["description"], Decimal("1"), "Pauschale", _price(rng, extra["price"]), "Sonstiges"
            ))
    return items


def make_document(rng: random.Random, kind: Kind, number: str, issue_date: date) -> Document:
    sender = make_sender(rng)
    job = rng.choice(load_data("jobs")["jobs"])
    common = dict(
        kind=kind,
        number=number,
        issue_date=issue_date,
        customer_no=f"K-{_digits(rng, 5)}",
        sender=sender,
        subject=job["subject"],
        location=rng.choice(job["locations"]),
        items=make_items(rng, job, sender.hourly_rate),
    )
    if kind == "angebot":
        return Document(**common, valid_until=issue_date + timedelta(days=30))
    end = issue_date - timedelta(days=rng.randint(1, 10))
    start = end - timedelta(days=rng.randint(2, 10)) if rng.random() < 0.5 else None
    return Document(
        **common,
        performance_start=start,
        performance_end=end,
        due_date=issue_date + timedelta(days=14),
        skonto_days=7 if rng.random() < 0.4 else None,
    )


def generate(seed: int, count: int, kinds: Iterable[Kind], today: date | None = None) -> list[Document]:
    """Generate `count` documents per kind; numbers ascend with the issue date."""
    rng = random.Random(seed)
    today = today or date.today()
    docs = []
    for kind in kinds:
        dates = sorted(today - timedelta(days=rng.randint(0, 364)) for _ in range(count))
        start = rng.randint(1, 800)
        for n, issue_date in enumerate(dates, start):
            number = f"{PREFIX[kind]}-{issue_date.year}-{n:04d}"
            docs.append(make_document(rng, kind, number, issue_date))
    return docs


def generate_pairs(seed: int, count: int, today: date | None = None) -> list[tuple[Document, Document]]:
    """Generate `count` matching (quote, invoice) pairs: same sender, job and items; the invoice cites the quote."""
    rng = random.Random(seed)
    today = today or date.today()
    quote_dates = sorted(today - timedelta(days=rng.randint(45, 364)) for _ in range(count))
    quote_start, invoice_start = rng.randint(1, 800), rng.randint(1, 800)
    pairs = []
    for n, quote_date in enumerate(quote_dates):
        quote = make_document(rng, "angebot", f"AN-{quote_date.year}-{quote_start + n:04d}", quote_date)
        end = quote_date + timedelta(days=rng.randint(7, 30))  # work done within the quote's validity
        start = end - timedelta(days=rng.randint(2, 6)) if rng.random() < 0.5 else None
        issue_date = end + timedelta(days=rng.randint(1, 10))
        invoice = replace(
            quote,
            kind="rechnung",
            number=f"RE-{issue_date.year}-{invoice_start + n:04d}",
            issue_date=issue_date,
            valid_until=None,
            performance_start=start,
            performance_end=end,
            due_date=issue_date + timedelta(days=14),
            skonto_days=7 if rng.random() < 0.4 else None,
            quote_number=quote.number,
            quote_date=quote.issue_date,
        )
        pairs.append((quote, invoice))
    return pairs
