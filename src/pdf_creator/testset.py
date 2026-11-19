"""Ground-truth test set for evaluation: quote/invoice pairs as PDFs, each with a JSON file of the correct values.

    uv run python -m pdf_creator.testset --pairs 50 --seed 42 --out testset

Files: angebot_0001.pdf/.json, rechnung_0001.pdf/.json, ... (same number = same pair).
30 % of the invoices deviate from their quote in exactly one way (DEVIATION_TYPES, equally often);
each document stays internally consistent. The invoice JSON lists the deviation under "deviations"
([] for clean invoices, null for quotes). Every JSON file has the same keys; money values are strings
with 2 decimals, dates are ISO strings.
"""

import argparse
import json
import random
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from pdf_creator.generator import generate_pairs
from pdf_creator.model import RECIPIENT, Document, LineItem, round_money
from pdf_creator.render import render

DEVIATION_TYPES = ("price_changed", "quantity_changed", "item_missing", "item_added")
DEVIATION_SHARE = Decimal("0.3")


@dataclass(frozen=True)
class Deviation:
    type: str
    quote_position: int | None  # 1-based position in the quote, None for item_added
    invoice_position: int | None  # 1-based position in the invoice, None for item_missing
    description: str
    quote_value: str | None = None  # unit price or quantity, only for price_changed / quantity_changed
    invoice_value: str | None = None


@dataclass(frozen=True)
class Case:
    pair_id: str
    quote: Document
    invoice: Document
    deviations: list[Deviation]


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{round_money(value):.2f}"


def _quantity(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def inject(rng: random.Random, kind: str, quote: Document, invoice: Document) -> tuple[Document, Document, Deviation]:
    """Return (quote, invoice, deviation) with one deviation of `kind` between quote and invoice."""
    items = list(invoice.items)
    match kind:
        case "price_changed":
            j = rng.randrange(len(items))
            old = items[j]
            new = replace(old, unit_price=round_money(old.unit_price * rng.randint(108, 125) / 100))
            items[j] = new
            return quote, replace(invoice, items=items), Deviation(
                kind, j + 1, j + 1, old.description, _money(old.unit_price), _money(new.unit_price)
            )
        case "quantity_changed":
            j = rng.choice([n for n, item in enumerate(items) if item.unit != "Pauschale"])
            old = items[j]
            step = Decimal("1") if old.unit == "Stk." else Decimal("0.5")
            new = replace(old, quantity=old.quantity + step * rng.randint(1, 4))
            items[j] = new
            return quote, replace(invoice, items=items), Deviation(
                kind, j + 1, j + 1, old.description, _quantity(old.quantity), _quantity(new.quantity)
            )
        case "item_missing":
            j = rng.randrange(len(items))
            removed = items.pop(j)
            return quote, replace(invoice, items=items), Deviation(kind, j + 1, None, removed.description)
        case "item_added":
            # Taken out of the quote instead of added to the invoice, so the invoice keeps a size that fits one page.
            j = rng.randrange(len(items))
            return replace(quote, items=items[:j] + items[j + 1:]), invoice, Deviation(
                kind, None, j + 1, items[j].description
            )
    raise ValueError(f"unknown deviation type: {kind}")


def build_cases(seed: int, count: int, today: date | None = None) -> list[Case]:
    pairs = generate_pairs(seed, count, today)
    rng = random.Random(f"deviations-{seed}")
    chosen = rng.sample(range(count), round(count * DEVIATION_SHARE))
    kinds = {i: DEVIATION_TYPES[n % len(DEVIATION_TYPES)] for n, i in enumerate(chosen)}
    cases = []
    for i, (quote, invoice) in enumerate(pairs):
        deviations = []
        if i in kinds:
            quote, invoice, deviation = inject(rng, kinds[i], quote, invoice)
            deviations = [deviation]
        cases.append(Case(f"{i + 1:04d}", quote, invoice, deviations))
    return cases


def _item(position: int, item: LineItem) -> dict:
    return {
        "position": position,
        "description": item.description,
        "quantity": _quantity(item.quantity),
        "unit": item.unit,
        "unit_price": _money(item.unit_price),
        "total": _money(item.total),
    }


def ground_truth(doc: Document, pair_id: str, deviations: list[Deviation] | None) -> dict:
    s = doc.sender
    return {
        "file": f"{doc.kind}_{pair_id}.pdf",
        "pair_id": pair_id,
        "doc_type": doc.kind,
        "number": doc.number,
        "issue_date": _date(doc.issue_date),
        "customer_no": doc.customer_no,
        "valid_until": _date(doc.valid_until),
        "performance_start": _date(doc.performance_start),
        "performance_end": _date(doc.performance_end),
        "due_date": _date(doc.due_date),
        "quote_number": doc.quote_number,
        "quote_date": _date(doc.quote_date),
        "sender": {
            "company": s.company,
            "street": s.street,
            "postal_code": s.postal_code,
            "city": s.city,
            "tax_number": s.tax_number,
            "vat_id": s.vat_id,
            "iban": s.iban,
            "bic": s.bic,
        },
        "recipient": list(RECIPIENT),
        "subject": doc.subject,
        "location": doc.location,
        "items": [_item(n, item) for n, item in enumerate(doc.items, 1)],
        "net": _money(doc.net),
        "vat": _money(doc.vat),
        "gross": _money(doc.gross),
        "skonto_amount": _money(doc.skonto_amount),
        "deviations": None if deviations is None else [vars(d) for d in deviations],
    }


def write_testset(cases: list[Case], out_dir: Path) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"Ausgabeordner ist nicht leer: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    for case in cases:
        for doc, deviations in ((case.quote, None), (case.invoice, case.deviations)):
            stem = f"{doc.kind}_{case.pair_id}"
            render(doc, out_dir).replace(out_dir / f"{stem}.pdf")
            data = ground_truth(doc, case.pair_id, deviations)
            (out_dir / f"{stem}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Erzeugt ein Testset (PDF + Ground-Truth-JSON) für die Evaluation.")
    parser.add_argument("--pairs", type=int, default=50, help="Anzahl Paare Angebot + Rechnung (Standard: 50)")
    parser.add_argument("--seed", type=int, help="Seed für reproduzierbare Ausgabe (Standard: zufällig)")
    parser.add_argument("--out", type=Path, default=Path("testset"), help="Ausgabeordner, muss leer sein (Standard: testset/)")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(2**32)
    print(f"Seed: {seed}")
    cases = build_cases(seed, args.pairs)
    try:
        write_testset(cases, args.out)
    except FileExistsError as e:
        parser.error(str(e))
    print(f"{len(cases)} Paare, davon {sum(bool(c.deviations) for c in cases)} mit Abweichung -> {args.out}")


if __name__ == "__main__":
    main()
