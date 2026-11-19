# Handoff: fictional-quotes-invoices-pdf-creator (fictional joinery quotes and invoices as PDFs + OpenAI data extension)

Project: `C:\Programming\fictional-quotes-invoices-pdf-creator` (no git repository). Project rules: `CLAUDE.md`, which takes precedence.
Original implementation plan: `C:\Users\cknau\.claude\plans\concurrent-noodling-crayon.md`. It is outdated on several
points; the current state is described here.

## Current state (everything works, 111 tests pass)

- Python 3.14 via **uv**. There is no system Python.
- Run the tests with **`uv run python -m pytest`**. Windows application control sometimes blocks `pytest.exe`.
  The command in `CLAUDE.md` still says `uv run pytest`.
- PDF generator: `uv run python -m pdf_creator --count N --type angebot|rechnung|both --seed S --out output`.
  `--pairs N` writes matching quote and invoice pairs to `output/paare/`.
- OpenAI extension: `uv sync --extra ai`, then `uv run python -m pdf_creator.ai --count N` writes
  `ai_output/senders.toml` and `ai_output/jobs.toml`. The default model is `gpt-5-mini` via `OPENAI_MODEL`.
  The key is in `.env` in the project root; the template is `.env.example`. Never show or log the key.
- The latest real API run succeeded: 3 jobs, all values valid, 20 test PDFs were all 1 page. **The user has not copied
  the `ai_output/` files to `src/pdf_creator/data/` yet. The user does that themselves; never copy them automatically.**

## Code layout (read the files for details)

- `src/pdf_creator/model.py`: Sender, LineItem, Document (Decimal, rounding).
- `generator.py`: seeded generator that reads `data/*.toml`. `make_domain` falls back to a surname-only domain.
  `generate_pairs` builds quote and invoice pairs.
- `render.py`: reportlab layout (DIN 5008, MUSTER watermark, footer). Contains the **layout checks**
  `sender_layout_problems`, `job_layout_problems`, `fits_amount`, `fits_description_line`, `fits_footer` and `drawable`.
  They use the same strings and positions as the drawing code (`_right_block_lines`, `_return_line`,
  `_footer_columns`, `_site_line`).
- `ai.py`: OpenAI Responses API with a strict JSON schema, then validation, then TOML in the exact style of `data/`
  (`build_senders`/`build_jobs` reproduce `data/` byte for byte, which a test proves).
- Tests: `tests/test_model.py`, `test_generator.py`, `test_render.py`, `test_ai.py`. The AI tests are offline and use
  a `FakeClient`.

## Decisions the user made (do not change them again)

- One trade only: joinery (Schreiner/Tischler). Five job types. Roles (Meister/Geselle/Azubi) were **removed
  entirely**; every labor item uses one hourly rate. Items are a flat numbered list with no group headings; totals
  appear only at the end. There is **no sign-off** ("Mit freundlichen Grüßen"). Documents are **1 page** (the renderer
  can still produce more for very long documents).
- AI output = **complete new files** containing only AI values (except `phone_blocks`, which are the reserved
  Bundesnetzagentur drama numbers). **No comparison with `data/`**: the AI may reuse values. Only duplicates within
  the answer are rejected.
- The prompt gets **one example** per section as a template.
- AI rules: hours ≤ 12 per item, prices ≤ 1200 €, quantities ≤ 100, whole numbers for Stk./Pauschale. The largest
  line total must fit the "Gesamt €" column (in practice < 100,000 €). Never use "Partner" in company names.
  `{first}` comes before `{surname}`. `einzel` patterns contain no legal-form word; `gmbh` patterns end only in
  " GmbH". Banks need "Muster" or "Beispiel" in the name and an 11-character test BIC. Postal codes 90000–97999 with
  a Franconian register court (Aschaffenburg excluded). **The AI output must pass `render.py`**: every combination of
  sender values is checked.
- Count ranges above the number of listed items are **corrected** ("korrigiert: …"), not rejected.
- Nothing is written if a section ends up empty or a file would be invalid. Both files are checked before either is
  written.

## Working style the user expects

- `CLAUDE.md`: before code changes, ask, present a short plan and **wait for approval**. Ask one question at a time.
  Ask before adding any new dependency.
- The user sometimes rejects `AskUserQuestion`. Clarifying questions in plain text are fine.
- The user decides on every real (paid) API call; ask before running one. Do not overwrite or copy files in `data/`.
- The user writes short German/English instructions with typos; read them carefully and ask when unsure.

## Open or possible next steps (only on request)

- In `ai_output/jobs.toml`, "Arbeitsplatte Schichtstoff 38 mm, walnut" has an English word. Mentioned to the user;
  nothing decided.
- The commands section in `CLAUDE.md` does not mention `--pairs`, `pdf_creator.ai` or `python -m pytest`. Offered,
  not done.
- Not a git repository. If one is created, `.env` belongs in `.gitignore`.

## Suggested skills

- `mattpocock-skills:grilling`: when a new feature needs planning (CLAUDE.md requires questions and a plan first).
- `mattpocock-skills:tdd`: for any change. The project works test-first, with pytest.
- `code-review`: after changes. Two reviews have run in this session; all findings were fixed.
- No `claude-api`: the extension uses **OpenAI** (`openai` package 3.x, Responses API), not Anthropic.
