# CLAUDE.md

## Project

A Python tool that generates **fictional sample PDFs** of **quotes (Angebote)** and **invoices (Rechnungen)**
for testing and demo purposes.

### Recipient (fixed)

Every document is addressed **to**:

```
MusterAI GmbH
Lange Str. 2
96052 Bamberg
```

Never change this recipient block.

### Senders (always fictional)

The companies that send the quotes and invoices are **invented**. Everything about them is made up:
company name, address, contact person, phone, email, website, tax number, VAT ID, bank details,
register court, products/services, prices, document numbers and dates.

Rules for fictional data:
- Never use names, addresses or identifiers of real existing companies or people.
- Use obviously fictional values for identifiers, e.g. IBANs starting with `DE00`, VAT IDs like `DE000000000`,
  phone numbers from reserved ranges, emails/websites on `example.com` / `example.de`.
- Fictional data should still look realistic and consistent (matching city and postal code,
  plausible prices, correct calculations).

### Content: craft and trade services (Handwerk)

All senders are fictional **craft businesses (Handwerksbetriebe)**, and all documents are about
craft work for the MusterAI GmbH office, e.g.:
- trades such as electrician, plumber/heating, painter, carpenter/joiner, roofer, tiler, floor layer,
  locksmith, drywall builder, gardener/landscaper
- typical jobs: renovations, repairs, maintenance, installations (e.g. new office lighting,
  repainting meeting rooms, fixing a leaking pipe, installing a kitchenette)

Line items should look like real craft quotes/invoices:
- labor (Arbeitsleistung) in hours × hourly rate, listed separately from material
- material (Material) with quantity, unit (Stk., m, m², l, Pauschale) and unit price
- optional typical items: travel flat fee (Anfahrtspauschale), disposal (Entsorgung),
  small parts (Kleinmaterial)
- realistic quantities, plausible German market prices, and a project/site description
  (Bauvorhaben / Leistungsort) at the MusterAI address

Ask during planning which trades and job types should be included and how many variants are needed.

### Marking as sample data

Every generated PDF must be clearly marked as a sample, so it can never be mistaken for a real document
(e.g. in bookkeeping):
- a visible watermark "MUSTER" across each page, and
- a footer line: "Fiktives Musterdokument – keine echte Rechnung / kein echtes Angebot".

Never generate documents without this marking.

## Tech constraints

- **Python 3.14 or higher** is required. Set `requires-python = ">=3.14"` in `pyproject.toml`.
- Use modern Python features where they make code simpler (type hints, `dataclasses`, `pathlib`, `match`).
- Use `decimal.Decimal` for all money values — never `float`.
- Keep dependencies minimal. Ask before adding any new dependency.

## Workflow: always ask while planning

Before writing or changing code:

1. **Ask clarifying questions** whenever anything is unclear or could be interpreted in more than one way.
   Ask one question at a time. Don't guess.
2. **Present a short plan** (what you will change, which files, how you will verify it).
3. **Wait for explicit approval** before implementing.

If something new comes up during implementation that changes the plan, stop and ask again.

## Behavioral rules (based on Karpathy's observations)

### 1. Think before coding
- State your assumptions explicitly. If you are unsure, ask.
- If there are several possible approaches, briefly show the options and their trade-offs instead of picking one silently.
- If something in the request seems wrong or contradictory, say so before starting.

### 2. Simplicity first
- Write the minimum code that solves the problem. Nothing speculative.
- No extra features, abstractions, config options, or "future-proofing" that wasn't asked for.
- If a solution can be written in 50 lines instead of 200, write the 50-line version.

### 3. Surgical changes
- Only touch the code needed for the task.
- Don't refactor, reformat, or "improve" unrelated code, comments, or files.
- Match the existing code style.
- If you notice unrelated problems, mention them — don't fix them unasked.

### 4. Goal-driven execution
- Turn each task into a verifiable goal (e.g. "a test that generates an invoice PDF with the correct totals passes").
- Write or update tests first where it makes sense, then implement until they pass.
- Before saying a task is done, verify it: run the tests and, for PDF changes, generate a sample PDF.

## Domain rules

- Quotes and invoices share the same layout and data model where possible (sender, recipient, line items, totals).
- Every document has a unique number, a date, recipient address, line items (description, quantity, unit price, total),
  net total, VAT, and gross total.
- Round money values to 2 decimal places only at the line-item total and final totals.
- Invoices should contain all fields required for German invoices (§14 UStG), filled with fictional values,
  so the samples are structurally complete.
- Sender data comes from fictional company profiles (e.g. a generator or a data file) — ask during planning
  how these should be created.

## Commands

<!-- Fill in once the project is set up -->
- Install: `uv sync`
- Run: `uv run python -m pdf_creator --count 5 --type both --seed 42 --out output`
- Ground-truth test set (PDF + JSON per document, 30 % invoices with one deviation from their quote):
  `uv run python -m pdf_creator.testset --pairs 50 --seed 42 --out testset` (output dir must be empty)
- Tests: `uv run pytest`
