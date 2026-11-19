"""Offline tests for the OpenAI extension (fake client, no network)."""

import json
import sys
import tomllib
from dataclasses import replace
from datetime import date
from decimal import Decimal
from importlib.resources import files
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from pdf_creator import ai, generator
from pdf_creator.generator import generate, load_data
from pdf_creator.model import LineItem
from pdf_creator.render import FOOTER_NOTE, fits_description_line, render

TODAY = date(2026, 11, 15)


class FakeClient:
    """Answers each request with the payload registered for its JSON schema name."""

    def __init__(self, **payloads: dict):
        self.payloads, self.calls = payloads, []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads[kwargs["text"]["format"]["name"]]
        return SimpleNamespace(output_text=json.dumps(payload, ensure_ascii=False))


def template(name: str) -> str:
    return (files("pdf_creator") / "data" / f"{name}.toml").read_text("utf-8")


GOOD_JOB = {
    "subject": "Einbau einer Akustik-Wandverkleidung",
    "locations": ["Besprechungsraum 1.12"],
    "labor_count": [2, 3],
    "material_count": [3, 4],
    "labor": [
        {"description": "Aufmaß vor Ort", "hours": ["1", "2"]},
        {"description": "Montage der Unterkonstruktion", "hours": ["4.0", "8"]},
        {"description": "Montage der Akustikpaneele", "hours": ["6", "10.50"]},
    ],
    "material": [
        {"description": "Akustikpaneele Eiche, 600 × 2400 mm", "unit": "m²", "qty": ["8.0", "16"], "step": "0.5",
         "price": ["89", "129.00"]},
        {"description": "Unterkonstruktion Holzlatten", "unit": "lfm", "qty": ["20", "40"], "step": "5",
         "price": ["2.1", "3.40"]},
        {"description": "Montagekleber", "unit": "Stk.", "qty": ["2", "4"], "step": "1.0", "price": ["8.90", "12.50"]},
        {"description": "Abschlussleisten", "unit": "lfm", "qty": ["6", "12"], "step": "1", "price": ["6.00", "9.00"]},
    ],
}
GOOD_EXTRAS = [
    {"description": "Baustellenreinigung", "probability": 0.6, "price": ["40.00", "90.00"]},
    {"description": "Abdeckarbeiten Bodenschutz", "probability": 0.4, "price": ["25", "55.5"]},
]


def senders_payload(**override) -> dict:
    return {
        "surnames": ["Hobelwanger", "Spanmeier", "müller", "Leimstätter", "Hobelwanger"],
        "first_names": ["Greta", "Anna", "Bruno"],
        "name_patterns": [
            {"pattern": "Werkstatt {surname}", "legal_form": "einzel"},
            {"pattern": "{surname} Möbel GmbH", "legal_form": "gmbh"},
            {"pattern": "{surname} Holz GmbH", "legal_form": "einzel"},  # wrong legal form
            {"pattern": "Schreinerei {surname}", "legal_form": "einzel"},  # also in data/: allowed
            {"pattern": "Tischlerei {surname} {first}", "legal_form": "einzel"},  # wrong name order
        ],
        "streets": ["Am Holzplatz", "Ringstraße 5"],
        "towns": [
            {"city": "Zapfendorf", "postal_code": "96199", "court": "Bamberg"},
            {"city": "Berlin", "postal_code": "10115", "court": "Berlin"},  # not Franconian
            {"city": "Hallstadt", "postal_code": "96103", "court": "Bamberg"},  # also in data/: allowed
            {"city": "Zapfendorf", "postal_code": "96199", "court": "Bamberg"},  # repeated in the answer
        ],
        "banks": [
            {"name": "Beispielbank Mainfranken eG", "bic": "BSPMDEW0XXX"},
            {"name": "Sparkasse Bamberg", "bic": "BYLADEM1SKB"},  # real-looking
        ],
        "hourly_rate": ["60", "78"],
        **override,
    }


VALID_SENDERS = {
    "surnames": ["Hobelwanger", "Spanmeier", "Leimstätter"],
    "first_names": ["Greta", "Anna", "Bruno"],
    "name_patterns": [
        {"pattern": "Werkstatt {surname}", "legal_form": "einzel"},
        {"pattern": "{surname} Möbel GmbH", "legal_form": "gmbh"},
        {"pattern": "Schreinerei {surname}", "legal_form": "einzel"},
    ],
    "streets": ["Am Holzplatz"],
    "towns": [
        {"city": "Zapfendorf", "postal_code": "96199", "court": "Bamberg"},
        {"city": "Hallstadt", "postal_code": "96103", "court": "Bamberg"},
    ],
    "banks": [{"name": "Beispielbank Mainfranken eG", "bic": "BSPMDEW0XXX"}],
    "hourly_rate": ["60", "78"],
}


# --- style ----------------------------------------------------------------------------------------

def test_builders_reproduce_the_data_files_exactly():
    """Writing the data files' own values with the template gives the identical file: same style."""
    assert ai.build_senders(template("senders"), load_data("senders")) == template("senders")
    assert ai.build_jobs(template("jobs"), load_data("jobs")) == template("jobs")


def test_built_files_contain_only_ai_values_and_the_template_comments():
    senders_text = ai.build_senders(template("senders"), VALID_SENDERS)
    jobs, _ = ai.validate_jobs({"jobs": [GOOD_JOB], "extras": GOOD_EXTRAS})
    jobs_text = ai.build_jobs(template("jobs"), jobs)

    senders, data = tomllib.loads(senders_text), load_data("senders")
    assert {k: senders[k] for k in ai.SENDER_KEYS} == VALID_SENDERS
    assert senders["phone_blocks"] == data["phone_blocks"]  # the only values kept from data/
    parsed = tomllib.loads(jobs_text)
    for job in parsed["jobs"]:  # step "1" is omitted in the file, like in data/
        job["material"] = [{**m, "step": m.get("step", "1")} for m in job["material"]]
        job["material"] = [{k: m[k] for k in ("description", "unit", "qty", "step", "price")} for m in job["material"]]
    assert parsed == jobs
    for text, name in ((senders_text, "senders"), (jobs_text, "jobs")):
        comments = [line for line in template(name).splitlines() if line.startswith("#")]
        assert [line for line in text.splitlines() if line.startswith("#")] == comments
    assert '  "Hobelwanger", "Spanmeier", "Leimstätter",\n]' in senders_text
    assert "\n\n# Extra items added to every job with the given probability.\n[[extras]]\n" in jobs_text


# --- senders --------------------------------------------------------------------------------------

def test_validate_senders_checks_only_the_ai_answer():
    """Values that also exist in data/ are allowed; only invalid or repeated entries are rejected."""
    new, rejected = ai.validate_senders(senders_payload())
    assert new == VALID_SENDERS
    # müller, Hobelwanger (repeat), 2 patterns, street with number, Berlin, Zapfendorf (repeat), real bank
    assert len(rejected) == 8
    assert sum("doppelt" in r for r in rejected) == 2
    assert any("{first} muss vor {surname}" in r for r in rejected)


@pytest.mark.parametrize("rate", [["30", "50"], ["80", "60"], ["58.5", "75"], ["60"]])
def test_invalid_hourly_rate_leaves_section_empty(rate):
    new, rejected = ai.validate_senders(senders_payload(hourly_rate=rate))
    assert new["hourly_rate"] == []
    assert any(r.startswith("hourly_rate") for r in rejected)


# --- jobs -----------------------------------------------------------------------------------------

def test_validate_jobs_normalizes_numbers_like_the_data_files():
    new, rejected = ai.validate_jobs({"jobs": [GOOD_JOB], "extras": GOOD_EXTRAS})
    assert rejected == []
    job = new["jobs"][0]
    assert job["labor"][1]["hours"] == ["4", "8"] and job["labor"][2]["hours"] == ["6", "10.5"]
    assert job["material"][0]["qty"] == ["8", "16"] and job["material"][0]["price"] == ["89.00", "129.00"]
    assert job["material"][1]["price"] == ["2.10", "3.40"]
    assert job["material"][2]["step"] == "1"
    assert new["extras"][1]["price"] == ["25.00", "55.50"]
    assert 'step = "1"' not in ai.format_job(job)  # omitted like in data/


def test_values_from_data_are_allowed_in_the_ai_output():
    """No comparison with data/: the AI may use the same subjects, texts and prices."""
    old = load_data("jobs")
    # data/ jobs in the shape of an AI answer (explicit step)
    jobs = [ai.job_template({**old, "jobs": old["jobs"][n:]})["jobs"][0] for n in range(len(old["jobs"]))]
    new, rejected = ai.validate_jobs({"jobs": jobs, "extras": old["extras"]})
    # some data/ jobs break AI-only rules (two-line texts, more than 12 hours); never because they exist in data/
    assert not any("doppelt" in r for r in rejected)
    assert len(new["jobs"]) >= 1
    assert {j["subject"] for j in new["jobs"]} <= {j["subject"] for j in old["jobs"]}
    assert [e["description"] for e in new["extras"]] == [e["description"] for e in old["extras"]]


def test_validate_jobs_rejects_invalid_and_repeated_entries():
    cases = {
        "too_many": {**GOOD_JOB, "subject": "Zu groß", "material": [
            {**GOOD_JOB["material"][0], "description": f"Platte {n}"} for n in range(11)],
            "material_count": [11, 11]},
        "bad_values": {**GOOD_JOB, "subject": "Kaputte Werte", "material": [
            {**GOOD_JOB["material"][0], "price": ["129.00", "89.00"]},
            {**GOOD_JOB["material"][1], "qty": ["20", "42"]},
            {**GOOD_JOB["material"][2], "unit": "Eimer"},
            {**GOOD_JOB["material"][3], "price": ["6.005", "9"]},
        ]},
        "repeated_item": {**GOOD_JOB, "subject": "Doppelte Position", "labor": [
            GOOD_JOB["labor"][1], {**GOOD_JOB["labor"][1], "hours": ["1", "2"]}, GOOD_JOB["labor"][2]]},
        "repeats_extra": {**GOOD_JOB, "subject": "Wiederholt Pauschale", "material": [
            {**GOOD_JOB["material"][0], "description": "Baustellenreinigung"}, *GOOD_JOB["material"][1:]]},
        "too_long": {**GOOD_JOB, "subject": "Zu lang", "labor": [
            {"description": "x" * 200, "hours": ["1", "2"]}, *GOOD_JOB["labor"][1:]]},
        "ok": GOOD_JOB,
        "repeated_subject": {**GOOD_JOB},
    }
    expected = ["eine Seite", "Nachkommastellen", "Position doppelt", "Position doppelt", "länger als eine Zeile",
                "Bauvorhaben doppelt"]
    new, rejected = ai.validate_jobs({"jobs": list(cases.values()), "extras": GOOD_EXTRAS})
    assert [j["subject"] for j in new["jobs"]] == [GOOD_JOB["subject"]]
    for message, reason in zip(rejected, expected, strict=True):
        assert reason in message
    for text in ("min > max", "Schrittweite", "Einheit"):
        assert text in rejected[1]


def test_same_price_range_in_two_items_is_fine():
    job = {**GOOD_JOB, "material": [{**m, "price": ["10.00", "20.00"]} for m in GOOD_JOB["material"]]}
    new, rejected = ai.validate_jobs({"jobs": [job], "extras": GOOD_EXTRAS})
    assert rejected == [] and len(new["jobs"]) == 1


def test_extras_are_limited_and_not_repeated():
    extras = [{"description": f"Pauschale {n}", "probability": 0.5, "price": ["10.00", "99.00"]} for n in range(1, 5)]
    new, rejected = ai.validate_jobs({"jobs": [], "extras": [extras[0], *extras]})
    assert [e["description"] for e in new["extras"]] == ["Pauschale 1", "Pauschale 2", "Pauschale 3"]
    assert "doppelt" in rejected[0] and "mehr als 3" in rejected[1]


# --- prompts --------------------------------------------------------------------------------------

def test_prompts_contain_only_one_example():
    jobs, senders = load_data("jobs"), load_data("senders")
    client = FakeClient(jobs={"jobs": [], "extras": []}, senders=senders_payload())
    ai.create_jobs(client, "m", 2, jobs)
    ai.create_senders(client, "m", 3, senders)
    job_prompt, sender_prompt = client.calls[0]["instructions"], client.calls[1]["instructions"]
    assert jobs["jobs"][0]["subject"] in job_prompt
    assert all(job["subject"] not in job_prompt for job in jobs["jobs"][1:])
    assert jobs["extras"][1]["description"] not in job_prompt
    assert senders["surnames"][0] in sender_prompt
    assert all(name not in sender_prompt for name in senders["surnames"][1:])
    assert all(town["city"] not in sender_prompt for town in senders["towns"][1:])
    assert all(c["text"]["format"]["strict"] is True for c in client.calls)


def test_templates_match_the_json_schema_shape():
    job = ai.job_template(load_data("jobs"))["jobs"][0]
    job_props = ai.JOBS_SCHEMA["properties"]["jobs"]["items"]["properties"]
    assert list(job) == list(job_props)
    assert all(list(m) == list(job_props["material"]["items"]["properties"]) for m in job["material"])
    assert list(ai.senders_template(load_data("senders"))) == list(ai.SENDERS_SCHEMA["properties"])


# --- complete run ---------------------------------------------------------------------------------

def run_main(monkeypatch, tmp_path, client) -> SystemExit | None:
    monkeypatch.setattr(ai, "make_client", lambda: (client, "fake-model"))
    monkeypatch.setattr(sys, "argv", ["ai", "--count", "2", "--out", str(tmp_path / "ai_output")])
    try:
        ai.main()
    except SystemExit as exit_:
        return exit_
    return None


def test_main_writes_complete_files_usable_for_pdfs(monkeypatch, tmp_path):
    client = FakeClient(senders=senders_payload(), jobs={"jobs": [GOOD_JOB], "extras": GOOD_EXTRAS})
    assert run_main(monkeypatch, tmp_path, client) is None
    out = tmp_path / "ai_output"
    senders = tomllib.loads((out / "senders.toml").read_text("utf-8"))
    jobs = tomllib.loads((out / "jobs.toml").read_text("utf-8"))

    # PDFs generated from the AI files use only AI data and keep all PDF rules
    original = generator.load_data
    monkeypatch.setattr(generator, "load_data", lambda n: {"senders": senders, "jobs": jobs}.get(n) or original(n))
    for doc in generate(seed=3, count=4, kinds=("angebot", "rechnung"), today=TODAY):
        assert doc.subject == GOOD_JOB["subject"]
        assert doc.sender.city in {"Zapfendorf", "Hallstadt"}
        assert doc.sender.bank_name == "Beispielbank Mainfranken eG"
        assert Decimal("60") <= doc.sender.hourly_rate <= Decimal("78")
        pages = [p.extract_text() for p in PdfReader(render(doc, tmp_path / "pdf")).pages]
        assert len(pages) == 1 and "MUSTER" in pages[0] and FOOTER_NOTE in pages[0]


def test_main_writes_nothing_when_a_section_is_empty(monkeypatch, tmp_path):
    client = FakeClient(senders=senders_payload(towns=[]), jobs={"jobs": [GOOD_JOB], "extras": GOOD_EXTRAS})
    exit_ = run_main(monkeypatch, tmp_path, client)
    assert exit_ is not None and "senders.towns" in str(exit_)
    assert not (tmp_path / "ai_output").exists()


def test_job_at_validation_limits_fits_on_one_page(tmp_path):
    """Worst case the validator allows: 15 rows (jobs + extras) with the longest one-line descriptions."""
    text = "Lorem ipsum dolor sit amet consectetur adipiscing elit " * 3
    longest = max((text[:n] for n in range(len(text))), key=lambda t: len(t) if fits_description_line(t) else 0)
    assert fits_description_line(longest) and not fits_description_line(text)
    base = generate(seed=1, count=1, kinds=("rechnung",), today=TODAY)[0]
    base = replace(base, skonto_days=7, quote_number="AN-2026-0001", quote_date=base.issue_date)
    items = [LineItem(longest, Decimal("12.5"), "m²", Decimal("999.99"), "Material")] * ai.MAX_ROWS
    assert len(PdfReader(render(replace(base, items=items), tmp_path)).pages) == 1


def test_missing_api_key_gives_clear_error(monkeypatch, tmp_path):
    pytest.importorskip("dotenv")
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        ai.make_client()


@pytest.mark.parametrize("pattern, legal_form", [
    ("Tischlerei {surname} & Partner", "einzel"),
    ("{first} {surname} & Partner", "einzel"),
    ("{surname} & Partner GmbH", "gmbh"),
    ("Schreinerei {surname} und PARTNER", "einzel"),
])
def test_partner_is_never_allowed_in_name_patterns(pattern, legal_form):
    payload = senders_payload(name_patterns=[{"pattern": pattern, "legal_form": legal_form}])
    new, rejected = ai.validate_senders(payload)
    assert new["name_patterns"] == []
    assert any("Partner" in r and "nicht erlaubt" in r for r in rejected)


def test_sender_prompt_forbids_partner():
    client = FakeClient(senders=senders_payload())
    ai.create_senders(client, "m", 3, load_data("senders"))
    assert "niemals 'Partner'" in client.calls[0]["instructions"]


# --- code review findings + plausibility limits ---------------------------------------------------

def test_nothing_is_written_when_one_file_would_be_invalid(monkeypatch, tmp_path):
    """Finding 1: both files are checked before either is written; old files stay untouched."""
    out = tmp_path / "ai_output"
    out.mkdir()
    (out / "senders.toml").write_text("old", encoding="utf-8")
    broken = {**GOOD_JOB, "labor": [{"description": "Montage \ud800", "hours": ["1", "2"]}, *GOOD_JOB["labor"][1:]]}
    client = FakeClient(senders=senders_payload(), jobs={"jobs": [broken], "extras": GOOD_EXTRAS})
    exit_ = run_main(monkeypatch, tmp_path, client)
    assert exit_ is not None and "Nichts geschrieben" in str(exit_)
    assert (out / "senders.toml").read_text(encoding="utf-8") == "old"
    assert not (out / "jobs.toml").exists()


@pytest.mark.parametrize("pattern", [
    "Schreinerei {surname",  # unbalanced bracket
    "{first!s} {surname}",  # conversion
    "{surname:>20} Holz",  # format spec
    "{surname} {surname}",  # placeholder twice
    "{0} {surname}",  # unknown placeholder
])
def test_malformed_patterns_are_rejected_not_crashing(pattern):
    """Finding 2."""
    new, rejected = ai.validate_senders(senders_payload(name_patterns=[{"pattern": pattern, "legal_form": "einzel"}]))
    assert new["name_patterns"] == [] and len(rejected) >= 1


def test_huge_values_are_rejected_not_crashing():
    """Finding 3."""
    job = {**GOOD_JOB, "material": [{**GOOD_JOB["material"][0], "qty": ["1e30", "2e30"], "step": "0.1"},
                                    *GOOD_JOB["material"][1:]]}
    new, rejected = ai.validate_jobs({"jobs": [job], "extras": GOOD_EXTRAS})
    assert new["jobs"] == [] and "Menge: mehr als 100" in rejected[0]


@pytest.mark.parametrize("rate", [["5²", "60"], ["٥٠", "60"], ["60", "70 "]])
def test_odd_digits_in_hourly_rate_are_rejected_not_crashing(rate):
    """Finding 4."""
    new, rejected = ai.validate_senders(senders_payload(hourly_rate=rate))
    assert new["hourly_rate"] == [] and any(r.startswith("hourly_rate") for r in rejected)


@pytest.mark.parametrize("response", [
    SimpleNamespace(status="incomplete", output_text='{"jobs": ['),
    SimpleNamespace(status="completed", output_text=""),
    SimpleNamespace(status="completed", output_text="Ich kann dabei nicht helfen."),
])
def test_incomplete_or_refused_answers_give_a_clear_message(response):
    """Finding 5."""
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: response))
    with pytest.raises(SystemExit, match="erneut starten"):
        ai.ask(client, "m", "i", "p", "jobs", ai.JOBS_SCHEMA)


@pytest.mark.parametrize("pattern, legal_form", [
    ("{surname} GmbH & Co. KG", "einzel"),
    ("Schreinerei {surname} KG", "einzel"),
    ("Tischlerei {surname} e.K.", "einzel"),
    ("{surname} Holzbau UG", "einzel"),
    ("{surname} UG GmbH", "gmbh"),
    ("{surname} GmbH & Co. KG", "gmbh"),
])
def test_legal_form_words_must_match_the_legal_form(pattern, legal_form):
    """Finding 6: 'einzel' has no legal-form suffix, 'gmbh' only ' GmbH' at the end."""
    payload = senders_payload(name_patterns=[{"pattern": pattern, "legal_form": legal_form}])
    new, rejected = ai.validate_senders(payload)
    assert new["name_patterns"] == [] and any("Rechtsform" in r for r in rejected)


@pytest.mark.parametrize("change, reason", [
    ({"labor": [{"description": "Montage", "hours": ["8", "12.5"]}]}, "mehr als 12"),
    ({"material": [{"description": "Theke", "unit": "Stk.", "qty": ["1", "1"], "step": "1",
                    "price": ["900.00", "1200.01"]}]}, "mehr als 1200"),
    ({"material": [{"description": "Scharniere", "unit": "Stk.", "qty": ["2", "4.5"], "step": "0.5",
                    "price": ["5.00", "9.00"]}]}, "ganzen Zahlen"),
])
def test_plausibility_limits(change, reason):
    """Limit 7: hours <= 12, prices <= 1200 EUR, Stk./Pauschale only in whole numbers."""
    job = {**GOOD_JOB, **change, "labor_count": [1, 1], "material_count": [1, 1]}
    if "labor" not in change:
        job["labor"] = GOOD_JOB["labor"][:1]
    if "material" not in change:
        job["material"] = GOOD_JOB["material"][:1]
    new, rejected = ai.validate_jobs({"jobs": [job], "extras": GOOD_EXTRAS})
    assert new["jobs"] == [] and reason in rejected[0]


def test_limits_themselves_are_allowed():
    job = {**GOOD_JOB, "labor_count": [1, 1], "material_count": [1, 1],
           "labor": [{"description": "Montage", "hours": ["8", "12"]}],
           "material": [{"description": "Theke", "unit": "Stk.", "qty": ["1", "2"], "step": "1",
                         "price": ["900.00", "1200.00"]}]}
    extra = {"description": "Sonderpauschale", "probability": 0.1, "price": ["800.00", "1200.01"]}
    new, rejected = ai.validate_jobs({"jobs": [job], "extras": [*GOOD_EXTRAS, extra]})
    assert len(new["jobs"]) == 1
    assert rejected == ["extras: 'Sonderpauschale' (mehr als 1200 in ['800.00', '1200.01'])"]


@pytest.mark.parametrize("override, key", [
    ({"name_patterns": [{"pattern": "{first} {surname} Möbelwerkstatt und Innenausbau GmbH",
                         "legal_form": "gmbh"}]}, "name_patterns"),
    ({"streets": ["Untere Hauptstraße am alten Sägewerk und Mühlbach"]}, "streets"),
    ({"towns": [{"city": "Sulzbach-Rosenberg am Oberen Pfälzer Wald und Naabtal-Süd", "postal_code": "92237",
                 "court": "Nürnberg"}]}, "towns"),
    ({"banks": [{"name": "Beispielbank Ober- und Mittelfranken Raiffeisen eG", "bic": "BSPLDEB0XXX"}]}, "banks"),
])
def test_names_must_fit_letterhead_and_footer(override, key):
    """Limit 8: company names, streets, towns and banks must fit their place in the PDF."""
    new, rejected = ai.validate_senders(senders_payload(**override))
    assert new[key] == [] and any("passt nicht ins PDF" in r for r in rejected)


def test_every_combination_of_accepted_sender_values_passes_render():
    """The generator may combine any kept values; render.py must accept every resulting sender."""
    from itertools import product
    from pdf_creator.render import sender_layout_problems
    long_values = senders_payload(
        surnames=["Hobelwanger", "Zirbelsteinmüllner", "Ast"],
        first_names=["Greta", "Maximiliane", "Bo"],
        name_patterns=[{"pattern": "Schreinerei {surname}", "legal_form": "einzel"},
                       {"pattern": "{first} {surname} Holzmanufaktur GmbH", "legal_form": "gmbh"},
                       {"pattern": "Werkstatt {surname}", "legal_form": "einzel"}],
        streets=["Am Holzplatz", "Untere Hauptstraße am Sägewerk", "Ring"],
        towns=[{"city": "Zapfendorf", "postal_code": "96199", "court": "Bamberg"},
               {"city": "Neustadt an der Aisch", "postal_code": "91413", "court": "Fürth"}],
        banks=[{"name": "Beispielbank Mainfranken eG", "bic": "BSPMDEW0XXX"}],
    )
    new, rejected = ai.validate_senders(long_values)
    assert any("passt nicht ins PDF" in r for r in rejected), "some long combinations must be rejected"
    pools = [new[k] for k in ai.LAYOUT_ORDER]
    assert all(pools)
    for combo in product(*pools):
        assert sender_layout_problems(ai.layout_sender(*combo)) == [], combo


def test_layout_sender_matches_the_generator(monkeypatch):
    """layout_sender builds the same strings the generator builds (company, email, register, owner)."""
    senders = {**load_data("senders"), **{k: v for k, v in VALID_SENDERS.items()}}
    original = generator.load_data
    monkeypatch.setattr(generator, "load_data", lambda n: senders if n == "senders" else original(n))
    import random
    for seed in range(200):
        made = generator.make_sender(random.Random(seed))
        surname = next(n for n in senders["surnames"] if n in made.company)
        pattern = next(p for p in senders["name_patterns"]
                       if p["pattern"].format(surname=surname, first=made.owner.split()[0]) == made.company)
        town = next(t for t in senders["towns"] if t["city"] == made.city)
        bank = next(b for b in senders["banks"] if b["name"] == made.bank_name)
        street = made.street.rsplit(" ", 1)[0]
        expected = ai.layout_sender(made.owner.split()[0], surname, pattern, street, town, bank)
        assert (expected.company, expected.email, expected.website, expected.owner) == (
            made.company, made.email, made.website, made.owner)
        assert (expected.register is None) == (made.register is None)


@pytest.mark.parametrize("text", ["Łukasz", "Ζωή"])
def test_names_the_pdf_font_cannot_draw_are_rejected(text):
    """Review finding: characters outside WinAnsi would render as empty boxes."""
    new, rejected = ai.validate_senders(senders_payload(first_names=[text, "Greta"]))
    assert text not in new["first_names"] and any("nicht darstellbare Zeichen" in r for r in rejected)


def test_job_texts_the_pdf_font_cannot_draw_are_rejected():
    job = {**GOOD_JOB, "labor": [{"description": "Montage → Abnahme", "hours": ["1", "2"]}, *GOOD_JOB["labor"][1:]]}
    new, rejected = ai.validate_jobs({"jobs": [job], "extras": GOOD_EXTRAS})
    assert new["jobs"] == [] and "nicht darstellbare Zeichen" in rejected[0]


@pytest.mark.parametrize("pattern", ["{surname} KGaA", "{surname} eG", "{surname} e.Kfm.", "{surname} gGmbH",
                                     "{surname} Ltd.", "{surname} SE", "{surname} UG (haftungsbeschränkt)"])
def test_more_legal_form_words_are_rejected_for_einzel(pattern):
    """Review finding: the first legal-form list missed these."""
    new, rejected = ai.validate_senders(senders_payload(name_patterns=[{"pattern": pattern, "legal_form": "einzel"}]))
    assert new["name_patterns"] == [] and any("Rechtsform" in r for r in rejected)


def test_ordinary_words_are_not_mistaken_for_legal_forms():
    patterns = [{"pattern": p, "legal_form": "einzel"} for p in
                ("Holzwerkstatt {surname}", "Schreinerei {surname} am See", "{first} {surname} Kontor")]
    new, _ = ai.validate_senders(senders_payload(name_patterns=patterns))
    assert new["name_patterns"] == patterns


def test_blank_and_padded_texts_are_handled():
    """Review finding: empty texts are rejected, surrounding spaces do not create 'different' entries."""
    blank = {**GOOD_JOB, "subject": "Leer", "labor": [{"description": "   ", "hours": ["1", "2"]},
                                                    *GOOD_JOB["labor"][1:]]}
    padded = {**GOOD_JOB, "subject": GOOD_JOB["subject"] + "  "}
    new, rejected = ai.validate_jobs({"jobs": [GOOD_JOB, blank, padded], "extras": GOOD_EXTRAS})
    assert [j["subject"] for j in new["jobs"]] == [GOOD_JOB["subject"]]
    assert "leer" in rejected[0] and "Bauvorhaben doppelt" in rejected[1]


def test_long_subject_or_location_is_rejected():
    """Review finding: Bauvorhaben and Leistungsort must stay on one line each (one-page rule)."""
    long_subject = {**GOOD_JOB, "subject": "Einbau " + "einer sehr langen Wandverkleidung " * 5}
    long_location = {**GOOD_JOB, "subject": "Anderer Job", "locations": ["Raum " + "mit Zusatz " * 12]}
    new, rejected = ai.validate_jobs({"jobs": [long_subject, long_location], "extras": GOOD_EXTRAS})
    assert new["jobs"] == []
    assert "Bauvorhaben länger als eine Zeile" in rejected[0] and "Leistungsort länger als eine Zeile" in rejected[1]


@pytest.mark.parametrize("qty, price, ok", [
    (["10", "100"], ["10.00", "999.99"], True),   # 99.999,00 fits
    (["10", "100"], ["10.00", "1200.00"], False),  # 120.000,00 does not fit the column
    (["10", "100.5"], ["10.00", "20.00"], False),  # more than 100
])
def test_quantity_limit_and_line_total_column(qty, price, ok):
    """Limit 100 per item; the largest line total must fit the 'Gesamt EUR' column."""
    job = {**GOOD_JOB, "material": [{**GOOD_JOB["material"][0], "qty": qty, "price": price},
                                    *GOOD_JOB["material"][1:]]}
    new, rejected = ai.validate_jobs({"jobs": [job], "extras": GOOD_EXTRAS})
    assert (len(new["jobs"]) == 1) is ok, rejected


def test_aschaffenburg_is_no_longer_offered():
    """Finding 10: Aschaffenburg's district uses 63xxx postal codes, which the check rejects."""
    assert "Aschaffenburg" not in ai.COURTS
    new, rejected = ai.validate_senders(senders_payload(towns=[
        {"city": "Alzenau", "postal_code": "63755", "court": "Aschaffenburg"}]))
    assert new["towns"] == [] and len(rejected) >= 1


# --- count ranges are corrected, not rejected ------------------------------------------------------

def test_count_range_above_listed_items_is_corrected():
    job = {**GOOD_JOB, "labor_count": [3, 5], "material_count": [6, 7]}  # 3 labor, 4 material listed
    new, messages = ai.validate_jobs({"jobs": [job], "extras": GOOD_EXTRAS})
    assert len(new["jobs"]) == 1
    assert new["jobs"][0]["labor_count"] == [3, 3] and new["jobs"][0]["material_count"] == [4, 4]
    assert messages == [
        "korrigiert: jobs: 'Einbau einer Akustik-Wandverkleidung' (labor_count [3, 5] -> [3, 3], nur 3 labor aufgeführt)",
        "korrigiert: jobs: 'Einbau einer Akustik-Wandverkleidung' (material_count [6, 7] -> [4, 4], "
        "nur 4 material aufgeführt)",
    ]


@pytest.mark.parametrize("counts", [{"labor_count": [5, 3]}, {"labor_count": [0, 2]}, {"material_count": [2]}])
def test_invalid_count_ranges_are_still_rejected(counts):
    new, messages = ai.validate_jobs({"jobs": [{**GOOD_JOB, **counts}], "extras": GOOD_EXTRAS})
    assert new["jobs"] == [] and "Anzahl-Bereich" in messages[-1]
    assert not any(m.startswith("korrigiert") for m in messages)


def test_corrected_job_still_follows_the_one_page_rule():
    material = [{**GOOD_JOB["material"][0], "description": f"Platte {n}", "price": [f"{10 + n}.00", "99.00"]}
                for n in range(11)]
    job = {**GOOD_JOB, "labor_count": [3, 9], "material_count": [11, 15], "material": material}
    new, messages = ai.validate_jobs({"jobs": [job], "extras": GOOD_EXTRAS})  # 3 + 11 > 13 rows allowed
    assert new["jobs"] == [] and "eine Seite" in messages[-1]


def test_main_prints_corrections_without_verworfen(monkeypatch, tmp_path, capsys):
    job = {**GOOD_JOB, "labor_count": [2, 5]}
    client = FakeClient(senders=senders_payload(), jobs={"jobs": [job], "extras": GOOD_EXTRAS})
    assert run_main(monkeypatch, tmp_path, client) is None
    out = capsys.readouterr().out
    assert "\nkorrigiert: jobs: 'Einbau einer Akustik-Wandverkleidung' (labor_count [2, 5] -> [2, 3]" in "\n" + out
    assert "verworfen: korrigiert" not in out
    assert tomllib.loads((tmp_path / "ai_output" / "jobs.toml").read_text("utf-8"))["jobs"][0]["labor_count"] == [2, 3]


def test_job_prompt_states_the_count_rule():
    client = FakeClient(jobs={"jobs": [], "extras": []})
    ai.create_jobs(client, "m", 1, load_data("jobs"))
    assert "max darf nie größer sein als die Anzahl" in client.calls[0]["instructions"]


def test_prompts_state_bic_and_hour_rules_precisely():
    client = FakeClient(senders=senders_payload(), jobs={"jobs": [], "extras": []})
    ai.create_senders(client, "m", 3, load_data("senders"))
    ai.create_jobs(client, "m", 1, load_data("jobs"))
    sender_prompt, job_prompt = client.calls[0]["instructions"], client.calls[1]["instructions"]
    assert "BIC genau 11 Zeichen" in sender_prompt
    assert ai.BIC.fullmatch("MUSTDEM0XXX") and ai.BIC.fullmatch("BSPLDEB0XXX")  # the prompt's examples are valid
    assert "jede einzelne Arbeitsleistung höchstens 12 Stunden" in job_prompt


@pytest.mark.parametrize("bic", ["MSTRDE0XXX", "BEISDE0XXX", "MUSTDEM1XXX", "mustdem0xxx"])
def test_bics_from_the_last_api_run_are_rejected(bic):
    """10-character BICs like in the last real run, a non-test BIC and lowercase stay invalid."""
    new, rejected = ai.validate_senders(senders_payload(banks=[{"name": "Musterbank Franken", "bic": bic}]))
    assert new["banks"] == [] and "Test-BIC" in rejected[-1]
