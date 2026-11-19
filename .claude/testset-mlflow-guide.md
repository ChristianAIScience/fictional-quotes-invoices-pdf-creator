# Guide: using the ground-truth test set with MLflow

This guide is for a **different project**, an agent or system that reads quotes (Angebote) and invoices (Rechnungen).
It explains what the test set contains and how to measure that system against it with MLflow.

The test set comes from `fictional-quotes-invoices-pdf-creator`
(`uv run python -m pdf_creator.testset --pairs 50 --seed 42 --out testset`). All documents are **fictional samples**.
Every PDF carries a "MUSTER" watermark and a sample footer. Never use them as real bookkeeping data.

## 1. What is in the test set

Current version: generated on 2026-11-17 with seed 42. It has 50 pairs (100 PDFs and 100 JSON files).
15 invoices deviate from their quote: 4 `price_changed`, 4 `quantity_changed`, 4 `item_missing` and 3 `item_added`.
The other 35 pairs are clean.

```
testset/
├── angebot_0001.pdf    angebot_0001.json    # quote of pair 0001
├── rechnung_0001.pdf   rechnung_0001.json   # invoice of pair 0001 (cites the quote)
└── ...                                       # same number = same pair (pair_id)
```

- Each PDF has a JSON file with the same name that holds the correct values (the ground truth).
- The PDFs are digital text PDFs (no scans), always 1 page, and all from fictional joinery businesses.
- The recipient is always `MusterAI GmbH, Lange Str. 2, 96052 Bamberg`.
- **Do not regenerate it casually.** Dates depend on the generation day, so the same seed on another day gives
  different values. Treat the `testset/` folder as a frozen dataset and log its hash with every run (see section 4).

## 2. JSON schema

Every JSON file has **the same keys**. A field that does not apply is `null`.

| Key | Type | Meaning |
|---|---|---|
| `file` | str | PDF file name, e.g. `rechnung_0001.pdf` |
| `pair_id` | str | `"0001"` … links quote and invoice |
| `doc_type` | `"angebot"` \| `"rechnung"` | document type |
| `number` | str | `AN-2026-0437` / `RE-2026-0037` |
| `issue_date` | ISO date | quote date or invoice date |
| `customer_no` | str | `K-12345` |
| `valid_until` | ISO date \| null | quote only |
| `performance_start` | ISO date \| null | invoice only, set only when the service spans a period |
| `performance_end` | ISO date \| null | invoice only: the performance date (or end of the period) |
| `due_date` | ISO date \| null | invoice only |
| `quote_number`, `quote_date` | str / ISO date \| null | invoice only: the quote it refers to |
| `sender` | object | `company, street, postal_code, city, tax_number, vat_id, iban, bic` |
| `recipient` | list[str] | the 3 address lines of the recipient |
| `subject`, `location` | str | Bauvorhaben / Leistungsort |
| `items` | list | `position` (1-based), `description`, `quantity`, `unit`, `unit_price`, `total` |
| `net`, `vat`, `gross` | str | money, 2 decimals, e.g. `"4297.23"` (VAT is always 19 %) |
| `skonto_amount` | str \| null | 2 % discount amount, if the invoice offers early-payment discount |
| `deviations` | list \| null | `null` for quotes, `[]` for clean invoices, otherwise exactly 1 entry |

**Formats:**
- Money and quantities are **strings**, so values stay exact. Read them with `decimal.Decimal`, never `float`.
- Dates are ISO (`YYYY-MM-DD`).
- The IBAN appears as printed, with spaces (`DE00 7868 0112 ...`).

**Differences between the PDF and the JSON:** The PDF shows German formats: `25.06.2026`, `4.297,23`, `1,5`.
Long item descriptions can wrap onto two lines. The word "MUSTER" (the watermark) appears in the extracted text and
is not document content. Normalize before you compare (section 3).

### Deviations

```json
"deviations": [{"type": "price_changed", "quote_position": 3, "invoice_position": 3,
                "description": "Topfscharniere mit Dämpfung", "quote_value": "7.20", "invoice_value": "8.50"}]
```

| `type` | What differs | `quote_position` | `invoice_position` | `quote_value` → `invoice_value` |
|---|---|---|---|---|
| `price_changed` | higher unit price in the invoice | n | n | unit prices |
| `quantity_changed` | higher quantity or hours in the invoice | n | n | quantities |
| `item_missing` | a quote item is missing from the invoice | n | `null` | `null` |
| `item_added` | the invoice has an item that is not in the quote | `null` | n | `null` |

**Rules for the evaluation:**
- Each document is arithmetically correct on its own. A deviation exists **only between quote and invoice**.
- Positions are 1-based and refer to the numbering printed in each PDF. After `item_missing`, the invoice items are
  renumbered.
- Each invoice has at most one deviation, and values only go up (the invoice charges too much).

## 3. Evaluation tasks and metrics

### Task A: extraction (per document, 100 cases)

The system reads one PDF and returns the fields. Compare them per field after normalizing:

```python
import json
from decimal import Decimal
from pathlib import Path

MONEY = {"net", "vat", "gross", "skonto_amount"}
FIELDS = ["doc_type", "number", "issue_date", "due_date", "quote_number",
          "sender.company", "sender.vat_id", "sender.iban", "net", "vat", "gross"]

def get(data: dict, path: str):
    for key in path.split("."):
        data = data.get(key) if isinstance(data, dict) else None
    return data

def norm(field: str, value):
    if value is None:
        return None
    if field in MONEY:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    if field == "sender.iban":
        return str(value).replace(" ", "").upper()
    return " ".join(str(value).split())

def score_extraction(truth: dict, pred: dict) -> dict[str, bool]:
    return {f: norm(f, get(truth, f)) == norm(f, get(pred, f)) for f in FIELDS}
```

The system should return dates as ISO, or you convert `DD.MM.YYYY` before comparing.

**Metrics:**
- accuracy per field (`acc_gross`, `acc_number`, …)
- `exact_match_rate`: share of documents with all fields correct
- `doc_type_accuracy`
- optional: item-level accuracy (count, descriptions, totals)

### Task B: deviation detection (per pair, 50 cases)

The system gets `angebot_XXXX.pdf` and `rechnung_XXXX.pdf` and reports its deviations. The truth is
`rechnung_XXXX.json["deviations"]`.

```python
def score_pair(truth: list[dict], pred: list[dict]) -> dict:
    t, p = truth[0] if truth else None, pred[0] if pred else None
    return {
        "actual": bool(t),
        "predicted": bool(p),
        "type_ok": bool(t and p and t["type"] == p.get("type")),
        "position_ok": bool(t and p and t["type"] == p.get("type")
                            and (t["quote_position"], t["invoice_position"])
                            == (p.get("quote_position"), p.get("invoice_position"))),
    }
```

**Metrics:**
- binary "has a deviation": `precision`, `recall`, `f1` and `false_positive_rate` (on the 35 clean pairs)
- `type_accuracy`: the type is correct, among the 15 true deviations
- `localization_accuracy`: type and position are correct
- recall per type (`recall_price_changed`, …), which shows which kind of error the system misses

Positive class = "deviation". With only 15 positive cases, one miss already costs about 7 points of recall. Report
the absolute counts (TP/FP/FN/TN) as well.

## 4. Logging with MLflow

Plain tracking works in every MLflow version. Compute the metrics in Python, then log them:

```python
import hashlib
import mlflow
import pandas as pd

def testset_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.glob("*")):
        h.update(p.name.encode()); h.update(p.read_bytes())
    return h.hexdigest()[:12]

root = Path("testset")
mlflow.set_experiment("document-agent-eval")
with mlflow.start_run(run_name="agent-v1"):
    mlflow.log_params({"testset_hash": testset_hash(root), "testset_seed": 42, "testset_pairs": 50,
                       "model": "...", "prompt_version": "..."})
    rows = []  # one dict per pair or document: pair_id, actual, predicted, type_ok, ... and the raw prediction
    # ... run the system and collect rows ...
    df = pd.DataFrame(rows)
    tp = int((df.actual & df.predicted).sum()); fp = int((~df.actual & df.predicted).sum())
    fn = int((df.actual & ~df.predicted).sum()); tn = int((~df.actual & ~df.predicted).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    mlflow.log_metrics({
        "precision": precision, "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "false_positive_rate": fp / (fp + tn) if fp + tn else 0.0,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    })
    mlflow.log_table(df, artifact_file="pair_results.json")  # per-case table in the MLflow UI
```

- Use **one run per system version** (model, prompt, pipeline), always against the same `testset_hash`.
  Then compare the runs in the MLflow UI.
- `log_table` keeps the individual cases. That makes it easy to see which pairs the system gets wrong.
- Optional: MLflow 3 has `mlflow.genai.evaluate(data=..., predict_fn=..., scorers=[...])` with custom scorers
  (`@scorer` from `mlflow.genai.scorers`, signature `(inputs, outputs, expectations)`). There, `inputs` would be the
  PDF paths and `expectations` the JSON values. Check the API of the installed MLflow version before you use it.

## 5. Limits of this test set

- It covers one trade (joinery), one layout, digital PDFs only (no OCR, no photos) and German only.
- Each invoice has at most one deviation, and only increases. Several deviations per invoice, price reductions and
  wrong arithmetic inside a document are not covered.
- 50 pairs give rough numbers only. For more stable metrics, generate a larger set once (`--pairs 200`) into a new,
  empty folder and log it under its own hash.
