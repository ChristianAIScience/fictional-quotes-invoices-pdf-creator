"""OpenAI extension: generate complete new data files (senders.toml, jobs.toml) for new PDFs.

    uv sync --extra ai
    uv run python -m pdf_creator.ai --count 5   # -> ai_output/senders.toml, ai_output/jobs.toml

The model gets one example per section as a template and answers in JSON (strict JSON schema).
The code validates the answer and writes it in exactly the structure, comments and style of the
files in src/pdf_creator/data/. The output is not compared with data/; every value comes from the
model, except phone_blocks: only the reserved "Drama Numbers" are allowed, so they are taken
unchanged. Nothing is written unless every section has valid entries. Review the files, then copy them to src/pdf_creator/data/ to create PDFs.
"""

import argparse
import json
import os
import re
import tomllib
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from pathlib import Path
from itertools import product
from string import Formatter

from pdf_creator.generator import load_data, make_domain
from pdf_creator.model import Sender, round_money
from pdf_creator.render import (
    drawable, fits_amount, fits_description_line, job_layout_problems, sender_layout_problems,
)

UNITS = ["Stk.", "m", "lfm", "m²", "l", "Pauschale"]
# One-page rule: max labor + max material + number of extras <= 15, each description on one line.
MAX_ROWS = 15
MAX_EXTRAS = 3
# Plausibility limits (CLAUDE.md: realistic quantities, plausible German market prices).
MAX_HOURS = Decimal("12")  # per labor item
MAX_PRICE = Decimal("1200")  # per unit price of material and extras, EUR net
MAX_QTY = Decimal("100")  # per material item, in its unit
WHOLE_UNITS = {"Stk.", "Pauschale"}  # counted in whole numbers
# Register courts in Franconia whose districts use postal codes 90000-97999 (Aschaffenburg uses 63xxx).
COURTS = {"Ansbach", "Bamberg", "Bayreuth", "Coburg", "Fürth", "Hof", "Nürnberg", "Schweinfurt", "Würzburg"}
LEGAL_FORM_WORDS = re.compile(  # legal-form words that may not appear in an 'einzel' name (or inside a GmbH name)
    r"(?<!\w)(g?GmbH|mbH|KGaA|KG|AG|SE|UG|OHG|GbR|PartG|PartGmbB|eG|e\.\s?(K|Kfm|Kfr)\.?|Ltd\.?|Inc\.?|LLC"
    r"|Co\.?|haftungsbeschränkt)(?!\w)"
)
BIC = re.compile(r"[A-Z]{4}DE[A-Z0-9]0([A-Z0-9]{3})?")  # second location char "0" = test BIC
RATE_LIMITS = (40, 100)
SENDER_KEYS = ("surnames", "first_names", "name_patterns", "streets", "towns", "banks", "hourly_rate")

RULES = (
    "Alle Firmen-, Personen- und Bankdaten sind fiktive Testdaten: niemals Namen realer Firmen oder Personen. "
    "Nachnamen sind erfunden: deutsch klingend, aber keine gängigen echten Nachnamen (nicht Müller, Schmidt, "
    "Meier usw.). Halte dich genau an das Muster der Vorlage, übernimm aber keine Werte daraus."
)


def _strings() -> dict:
    return {"type": "array", "items": {"type": "string"}}


def _object(**properties) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


SENDERS_SCHEMA = _object(
    surnames=_strings(),
    first_names=_strings(),
    name_patterns={"type": "array", "items": _object(
        pattern={"type": "string"}, legal_form={"type": "string", "enum": ["einzel", "gmbh"]},
    )},
    streets=_strings(),
    towns={"type": "array", "items": _object(
        city={"type": "string"}, postal_code={"type": "string"}, court={"type": "string"},
    )},
    banks={"type": "array", "items": _object(name={"type": "string"}, bic={"type": "string"})},
    hourly_rate=_strings(),
)

JOBS_SCHEMA = _object(
    jobs={"type": "array", "items": _object(
        subject={"type": "string"},
        locations=_strings(),
        labor_count={"type": "array", "items": {"type": "integer"}},
        material_count={"type": "array", "items": {"type": "integer"}},
        labor={"type": "array", "items": _object(description={"type": "string"}, hours=_strings())},
        material={"type": "array", "items": _object(
            description={"type": "string"},
            unit={"type": "string", "enum": UNITS},
            qty=_strings(),
            step={"type": "string"},
            price=_strings(),
        )},
    )},
    extras={"type": "array", "items": _object(
        description={"type": "string"}, probability={"type": "number"}, price=_strings(),
    )},
)


# --- OpenAI ---------------------------------------------------------------------------------------

def make_client():
    """Return (client, model) from .env in the current directory / environment."""
    from dotenv import load_dotenv

    load_dotenv(Path(".env"))
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or key == "sk-your-key-here":
        raise SystemExit("OPENAI_API_KEY fehlt: .env anlegen (Vorlage: .env.example).")
    from openai import OpenAI

    return OpenAI(api_key=key), os.environ.get("OPENAI_MODEL", "gpt-5-mini")


def ask(client, model: str, instructions: str, prompt: str, name: str, schema: dict) -> dict:
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=prompt,
        text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
    )
    status = getattr(response, "status", "completed")
    text = getattr(response, "output_text", "") or ""
    if status != "completed" or not text:
        raise SystemExit(f"Keine vollständige Antwort vom Modell (Status {status!r}). Bitte erneut starten.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise SystemExit(f"Antwort des Modells ist kein gültiges JSON ({error}). Bitte erneut starten.") from None


# --- validation -----------------------------------------------------------------------------------

def _decimal(value: str) -> Decimal | None:
    try:
        d = Decimal(value)
    except InvalidOperation:
        return None
    return d if d.is_finite() and d > 0 else None


def _range(values: list[str], step: Decimal | None = None, places: int | None = None,
           maximum: Decimal | None = None) -> str | None:
    """Error text for an invalid [min, max] range of decimal strings, else None."""
    if len(values) != 2 or None in (nums := [_decimal(v) for v in values]):
        return f"ungültiger Bereich {values}"
    lo, hi = nums
    if lo > hi:
        return f"min > max in {values}"
    if maximum is not None and hi > maximum:
        return f"mehr als {maximum} in {values}"
    if places is not None and any(n.as_tuple().exponent < -places for n in nums):
        return f"mehr als {places} Nachkommastellen in {values}"
    try:
        off_step = step is not None and bool(lo % step or (hi - lo) % step)
    except InvalidOperation:  # values far too large for the step
        return f"ungültiger Bereich {values}"
    if off_step:
        return f"{values} passt nicht zur Schrittweite {step}"
    return None


def _count(values: list[int], pool: list) -> bool:
    return len(values) == 2 and 1 <= values[0] <= values[1] <= len(pool)


def _number(value: str) -> str:
    """'6.0' -> '6', '10.50' -> '10.5' (style of the data files)."""
    return f"{Decimal(value).normalize():f}"


def _money(value: str) -> str:
    """'6.5' -> '6.50' (prices always with 2 decimals, like the data files)."""
    return f"{Decimal(value):.2f}"


class _Collector:
    """Collects valid entries per key; an entry repeated within the AI answer is kept only once."""

    def __init__(self, keys: tuple[str, ...]):
        self.seen = {k: set() for k in keys}
        self.new = {k: [] for k in keys}
        self.rejected: list[str] = []

    def add(self, key: str, value, marker: str, error: str | None) -> None:
        if error:
            self.rejected.append(f"{key}: {value!r} ({error})")
        elif marker.casefold() in self.seen[key]:
            self.rejected.append(f"{key}: {value!r} (doppelt)")
        else:
            self.seen[key].add(marker.casefold())
            self.new[key].append(value)


def _pattern_error(p: dict) -> str | None:
    pattern = p["pattern"]
    try:
        parsed = [(field, spec, conversion) for _, field, spec, conversion in Formatter().parse(pattern)
                  if field is not None]
    except ValueError:
        return "ungültiges Muster (Klammern)"
    fields = [field for field, _, _ in parsed]
    if any(spec or conversion for _, spec, conversion in parsed):
        return "Platzhalter ohne Format-Angaben verwenden"
    if "surname" not in fields or not set(fields) <= {"surname", "first"} or len(fields) != len(set(fields)):
        return "braucht {surname}, erlaubt sind nur {surname} und {first} (je einmal)"
    if "first" in fields and fields.index("first") > fields.index("surname"):
        return "{first} muss vor {surname} stehen"
    if "partner" in pattern.casefold():
        return "'Partner' ist nicht erlaubt"
    gmbh = p["legal_form"] == "gmbh"
    rest = pattern.removesuffix(" GmbH") if gmbh else pattern
    if gmbh != pattern.endswith(" GmbH") or LEGAL_FORM_WORDS.search(rest):
        return "Rechtsform-Zusatz passt nicht (einzel: keiner, gmbh: nur ' GmbH' am Ende)"
    return None


LAYOUT_ORDER = ("first_names", "surnames", "name_patterns", "streets", "towns", "banks")
PLACEHOLDERS = {  # narrowest values for categories that have no accepted entry yet
    "first_names": "I", "surnames": "I", "name_patterns": {"pattern": "{surname}", "legal_form": "einzel"},
    "streets": "I", "towns": {"city": "I", "postal_code": "90000", "court": "Hof"},
    "banks": {"name": "I", "bic": "IIIIDEI0"},
}


def layout_sender(first: str, surname: str, pattern: dict, street: str, town: dict, bank: dict) -> Sender:
    """The sender the generator would build from these values, with the widest variable parts."""
    company = pattern["pattern"].format(surname=surname, first=first)
    domain = make_domain(company, surname)
    return Sender(
        company=company, legal_form=pattern["legal_form"], owner=f"{first} {surname}", contact=f"{first} {surname}",
        street=f"{street} 60", postal_code=town["postal_code"], city=town["city"], phone="030 23125 000",
        email=f"info@{domain}", website=f"www.{domain}", tax_number="000/000/00000", vat_id="DE000000000",
        register=f"Amtsgericht {town['court']}, HRB 00000" if pattern["legal_form"] == "gmbh" else None,
        bank_name=bank["name"], iban="DE00 0000 0000 0000 0000 00", bic=bank["bic"], hourly_rate=Decimal("60"),
    )


def _fit_layout(entries: dict) -> tuple[dict, list[str]]:
    """Keep entries whose every combination with the entries kept so far passes render.py's layout check."""
    kept = {k: [] for k in LAYOUT_ORDER}
    rejected = []
    for key in LAYOUT_ORDER:
        for candidate in entries[key]:
            pools = [[candidate] if k == key else kept[k] or [PLACEHOLDERS[k]] for k in LAYOUT_ORDER]
            problems = next((pr for combo in product(*pools) if (pr := sender_layout_problems(layout_sender(*combo)))),
                            None)
            if problems:
                rejected.append(f"{key}: {candidate!r} (passt nicht ins PDF: {problems[0]})")
            else:
                kept[key].append(candidate)
    return kept, rejected


def validate_senders(new: dict) -> tuple[dict, list[str]]:
    """Valid entries of the AI answer per key and the rejected ones (no comparison with data/).

    Every combination of the kept values is checked with render.sender_layout_problems, so any sender the
    generator builds from the file fits the PDF.
    """
    c = _Collector(SENDER_KEYS[:-1])
    for key in ("first_names", "surnames"):
        for name in (n.strip() for n in new[key]):
            c.add(key, name, name, None if name.isalpha() and name[0].isupper() else "kein einzelner Name")
    for p in new["name_patterns"]:
        p = {"pattern": p["pattern"].strip(), "legal_form": p["legal_form"]}
        c.add("name_patterns", p, p["pattern"], _pattern_error(p))
    for street in (s.strip() for s in new["streets"]):
        c.add("streets", street, street, None if street and not any(ch.isdigit() for ch in street)
              else "leer oder mit Hausnummer")
    for t in new["towns"]:
        t = {k: t[k].strip() for k in ("city", "postal_code", "court")}
        ok = t["city"] and re.fullmatch(r"9[0-7][0-9]{3}", t["postal_code"]) and t["court"] in COURTS
        c.add("towns", t, t["city"], None if ok else "PLZ nicht 90000-97999 oder unbekanntes Registergericht")
    for b in new["banks"]:
        b = {"name": b["name"].strip(), "bic": b["bic"].strip()}
        ok = ("Muster" in b["name"] or "Beispiel" in b["name"]) and BIC.fullmatch(b["bic"])
        c.add("banks", b, b["name"], None if ok else "Name ohne Muster/Beispiel oder keine Test-BIC")
    kept, layout_rejected = _fit_layout(c.new)

    rate = new["hourly_rate"]
    valid = len(rate) == 2 and all(re.fullmatch(r"[0-9]+", r) for r in rate) and (
        RATE_LIMITS[0] <= int(rate[0]) <= int(rate[1]) <= RATE_LIMITS[1])
    if not valid:
        c.rejected.append(f"hourly_rate: {rate!r} (ganze Euro, {RATE_LIMITS[0]}-{RATE_LIMITS[1]}, min <= max)")
    return {**kept, "hourly_rate": [str(int(r)) for r in rate] if valid else []}, c.rejected + layout_rejected


def _strip_job(job: dict) -> dict:
    """Job with surrounding whitespace removed from every text."""
    return {
        **job,
        "subject": job["subject"].strip(),
        "locations": [loc.strip() for loc in job["locations"]],
        "labor": [{**l, "description": l["description"].strip()} for l in job["labor"]],
        "material": [{**m, "description": m["description"].strip()} for m in job["material"]],
    }


def _job_errors(job: dict, max_items: int) -> list[str]:
    errors = []
    texts = [i["description"] for i in job["labor"] + job["material"]]
    if not job["subject"] or not job["locations"] or not all(job["locations"]) or not all(texts):
        errors.append("Bauvorhaben, Räume oder Beschreibungen leer")
    errors += [p for loc in job["locations"] or [""] for p in job_layout_problems(job["subject"], loc, texts)]
    if not job["labor"] or not job["material"]:
        errors.append("Arbeitsleistung oder Material fehlt")
    if not _count(job["labor_count"], job["labor"]) or not _count(job["material_count"], job["material"]):
        errors.append("Anzahl-Bereich passt nicht zur Auswahl")
    elif job["labor_count"][1] + job["material_count"][1] > max_items:
        errors.append(f"mehr als {max_items} Positionen möglich (passt nicht auf eine Seite)")
    for labor in job["labor"]:
        if error := _range(labor["hours"], step=Decimal("0.5"), maximum=MAX_HOURS):
            errors.append(f"Stunden: {error}")
    for mat in job["material"]:
        if mat["unit"] not in UNITS:
            errors.append(f"unbekannte Einheit {mat['unit']!r}")
        if (step := _decimal(mat["step"])) is None:
            errors.append(f"ungültige Schrittweite {mat['step']!r}")
        elif mat["unit"] in WHOLE_UNITS and step != step.to_integral_value():
            errors.append(f"{mat['unit']} nur in ganzen Zahlen")
        elif error := _range(mat["qty"], step=step, maximum=MAX_QTY):
            errors.append(f"Menge: {error}")
        if error := _range(mat["price"], places=2, maximum=MAX_PRICE):
            errors.append(f"Preis: {error}")
        elif not _range(mat["qty"], maximum=MAX_QTY):
            largest = round_money(Decimal(mat["qty"][1]) * Decimal(mat["price"][1]))
            if not fits_amount(largest):
                errors.append(f"Gesamtbetrag bis {largest} € passt nicht in die Spalte")
    return errors


def _repeated(job: dict, extra_texts: set[str]) -> list[str]:
    """Errors for items that repeat within the job or repeat one of the extras."""
    errors, seen = [], set()
    for item in job["labor"] + job["material"]:
        text = item["description"].casefold()
        if text in seen or text in extra_texts:
            errors.append(f"Position doppelt: {item['description']!r}")
        seen.add(text)
    return errors


def _normalize_job(job: dict) -> dict:
    return {
        **job,
        "labor": [{**l, "hours": [_number(h) for h in l["hours"]]} for l in job["labor"]],
        "material": [
            {**m, "qty": [_number(q) for q in m["qty"]], "step": _number(m["step"]),
             "price": [_money(p) for p in m["price"]]}
            for m in job["material"]
        ],
    }


def validate_jobs(new: dict) -> tuple[dict, list[str]]:
    """Valid jobs and extras of the AI answer and the rejected ones (no comparison with data/).

    At most MAX_EXTRAS extras; every job must fit on one page together with all extras.
    """
    rejected: list[str] = []
    extras, extra_texts = [], set()
    for e in new["extras"]:
        e = {**e, "description": e["description"].strip()}
        text_ok = e["description"] and drawable(e["description"]) and fits_description_line(e["description"])
        error = _range(e["price"], places=2, maximum=MAX_PRICE) or (
            None if 0 < e["probability"] <= 1 and text_ok else "Wahrscheinlichkeit oder Text")
        if not error and e["description"].casefold() in extra_texts:
            error = "doppelt"
        if not error and len(extras) == MAX_EXTRAS:
            error = f"mehr als {MAX_EXTRAS} Pauschalen"
        if error:
            rejected.append(f"extras: {e['description']!r} ({error})")
        else:
            extra_texts.add(e["description"].casefold())
            extras.append({**e, "price": [_money(p) for p in e["price"]]})

    max_items = MAX_ROWS - max(len(extras), 1)
    subjects, jobs = set(), []
    for job in map(_strip_job, new["jobs"]):
        job, corrections = _fix_counts(job)
        rejected += corrections
        errors = _job_errors(job, max_items) + _repeated(job, extra_texts)
        if job["subject"].casefold() in subjects:
            errors.append("Bauvorhaben doppelt")
        if errors:
            rejected.append(f"jobs: {job['subject']!r} ({'; '.join(errors)})")
        else:
            subjects.add(job["subject"].casefold())
            jobs.append(_normalize_job(job))
    return {"jobs": jobs, "extras": extras}, rejected


def _fix_counts(job: dict) -> tuple[dict, list[str]]:
    """Lower a labor/material count range that asks for more items than listed (e.g. [4, 5] with 4 items -> [4, 4]).

    Returns the job and one "korrigiert: ..." note per correction.
    """
    notes = []
    for count, pool in (("labor_count", "labor"), ("material_count", "material")):
        values, available = job[count], len(job[pool])
        if len(values) == 2 and available and 1 <= values[0] <= values[1] and values[1] > available:
            fixed = [min(values[0], available), available]
            notes.append(f"korrigiert: jobs: {job['subject']!r} ({count} {values} -> {fixed}, "
                         f"nur {available} {pool} aufgeführt)")
            job = {**job, count: fixed}
    return job, notes


def missing_sections(senders: dict, jobs: dict) -> list[str]:
    """Sections without a single valid entry; such files would not work for PDFs."""
    return [f"senders.{k}" for k in SENDER_KEYS if not senders[k]] + [f"jobs.{k}" for k in jobs if not jobs[k]]


# --- prompts --------------------------------------------------------------------------------------

def senders_template(data: dict) -> dict:
    """One example per list (in the JSON schema shape) as a template for the model."""
    return {k: data[k] if k == "hourly_rate" else data[k][:1] for k in SENDER_KEYS}


def job_template(data: dict) -> dict:
    """One example job and one extra (in the JSON schema shape, with explicit step) as a template."""
    job = data["jobs"][0]
    material = [{k: m.get(k, "1") for k in ("description", "unit", "qty", "step", "price")} for m in job["material"]]
    return {"jobs": [{**job, "material": material}], "extras": data["extras"][:1]}


def create_senders(client, model: str, count: int, data: dict) -> tuple[dict, list[str]]:
    instructions = (
        f"Du erzeugst die Absender-Daten fiktiver Schreinereien/Tischlereien in Franken. {RULES} "
        "Namensmuster nutzen {surname} und optional {first}; legal_form 'gmbh' endet auf ' GmbH', 'einzel' "
        "enthält keinen Rechtsform-Zusatz (kein GmbH, KG, UG, Co.); {first} steht immer vor {surname}; niemals 'Partner' oder '& Partner' verwenden. "
        "Straßen: generische deutsche Straßennamen ohne Hausnummer. Orte (towns): echte "
        "Orte in Franken mit korrekter Postleitzahl und dem zuständigen Registergericht (court, nur der Ortsname, "
        f"einer von {sorted(COURTS)}; PLZ 90000-97999). Banken: offensichtlich fiktiv, Name enthält 'Muster' oder 'Beispiel', "
        "BIC genau 11 Zeichen, nur Großbuchstaben/Ziffern: 4 Buchstaben Bankkürzel + 'DE' + 2 Ortszeichen, "
        "davon das zweite eine 0 (Test-BIC) + 3 Zeichen Filiale, z. B. 'MUSTDEM0XXX' oder 'BSPLDEB0XXX'; "
        "zähle die Zeichen nach. hourly_rate: "
        "[min, max] Netto-Stundensatz in ganzen Euro, realistisch für Schreinereien. Vorlage mit einem Beispiel "
        "pro Liste: " + json.dumps(senders_template(data), ensure_ascii=False)
    )
    some = max(3, count // 2)
    prompt = (
        f"Erzeuge nach genau diesem Muster {count} Nachnamen, {count} Vornamen, {some} Namensmuster "
        f"(einzel und gmbh gemischt), {count} Straßen, {some} Orte, 2 Banken und eine hourly_rate."
    )
    return validate_senders(ask(client, model, instructions, prompt, "senders", SENDERS_SCHEMA))


def create_jobs(client, model: str, count: int, data: dict) -> tuple[dict, list[str]]:
    instructions = (
        "Du erzeugst Vorlagen für Angebote/Rechnungen fiktiver Schreinereien (Tischlereien) für Arbeiten im Büro "
        f"der MusterAI GmbH in Bamberg. {RULES} Jede Vorlage hat: Bauvorhaben (subject), passende Räume "
        "(locations), Arbeitsleistungen (description, hours=[min,max] in 0,5-Schritten; jede einzelne Arbeitsleistung höchstens 12 Stunden, "
        "größere Arbeiten in mehrere Positionen aufteilen) und Material "
        f"(description, unit aus {UNITS}, qty=[min,max] passend zur Schrittweite step, step='1' für Stückzahlen, "
        "Menge höchstens 100, price=[min,max] Netto-Einzelpreis in EUR mit Punkt, max. 2 Nachkommastellen, höchstens 1200). labor_count/material_count "
        "= [min,max], wie viele Positionen gewählt werden; max darf nie größer sein als die Anzahl der "
        "aufgeführten labor- bzw. material-Einträge. extras sind Pauschalen, die zu jedem Auftrag kommen "
        "können (description, probability 0-1, price=[min,max]); Anfahrt, Kleinmaterial wie Schrauben/Dübel und "
        "Entsorgung gehören nur in extras, nicht ins Material. Realistische deutsche Marktpreise; kurze "
        "Beschreibungen (höchstens ca. 55 Zeichen, eine Tabellenzeile) ohne Rollen- oder Personenangaben. "
        "Vorlage mit einem Beispiel: " + json.dumps(job_template(data), ensure_ascii=False)
    )
    prompt = (
        f"Erzeuge nach genau diesem Muster {count} unterschiedliche Vorlagen und {MAX_EXTRAS} extras, mit "
        f"anderen Bauvorhaben, Beschreibungen und eigenen, fiktiven Preisen. max labor_count + max material_count "
        f"höchstens {MAX_ROWS - MAX_EXTRAS}."
    )
    return validate_jobs(ask(client, model, instructions, prompt, "jobs", JOBS_SCHEMA))


# --- TOML output in the style of the data files ---------------------------------------------------

def _value(v) -> str:
    match v:
        case bool():
            return "true" if v else "false"
        case int() | float():
            return repr(v)
        case str():
            return json.dumps(v, ensure_ascii=False)  # JSON string escapes are valid TOML basic strings
        case dict():
            return "{ " + ", ".join(f"{k} = {_value(x)}" for k, x in v.items()) + " }"
        case list():
            return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"not serializable: {v!r}")


def format_job(job: dict) -> str:
    material = [{k: v for k, v in m.items() if not (k == "step" and v == "1")} for m in job["material"]]
    return (
        "[[jobs]]\n"
        + "".join(f"{k} = {_value(job[k])}\n" for k in ("subject", "locations", "labor_count", "material_count"))
        + "labor = [\n" + "".join(f"  {_value(x)},\n" for x in job["labor"]) + "]\n"
        + "material = [\n" + "".join(f"  {_value(x)},\n" for x in material) + "]\n"
    )


def format_extra(extra: dict) -> str:
    return "[[extras]]\n" + "".join(f"{k} = {_value(extra[k])}\n" for k in ("description", "probability", "price"))


def _replace_array(text: str, key: str, values: list) -> str:
    """Replace the items of a multi-line `key = [ ... ]` array, keeping the items-per-line of the template."""
    m = re.search(rf"^{key} = \[\n(.*?)^\]$", text, re.S | re.M)
    if values and isinstance(values[0], dict):
        body = "".join(f"  {_value(v)},\n" for v in values)
    else:
        per_line = m.group(1).splitlines()[0].count(",")
        body = "".join(
            "  " + ", ".join(_value(v) for v in values[i:i + per_line]) + ",\n"
            for i in range(0, len(values), per_line)
        )
    return text[:m.start(1)] + body + text[m.end(1):]


def build_senders(template: str, senders: dict) -> str:
    """senders.toml in the style of `template` with all values from `senders` (phone_blocks unchanged)."""
    for key in ("surnames", "first_names", "name_patterns", "streets", "towns", "banks"):
        template = _replace_array(template, key, senders[key])
    rate = f"hourly_rate = {_value(senders['hourly_rate'])}"
    return re.sub(r"^hourly_rate = .*$", lambda _: rate, template, flags=re.M)


def build_jobs(template: str, jobs: dict) -> str:
    """jobs.toml in the style of `template`: its header and comments, all jobs and extras from `jobs`."""
    header = template[:template.index("[[jobs]]")]
    extras_comment = re.search(r"^(#.*\n)*(?=\[\[extras\]\])", template, re.M).group(0)
    return (
        header
        + "\n".join(format_job(job) for job in jobs["jobs"])
        + "\n" + extras_comment
        + "\n".join(format_extra(e) for e in jobs["extras"])
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Erzeugt mit OpenAI neue, vollständige senders.toml und jobs.toml.")
    parser.add_argument("--count", type=int, default=5, help="Anzahl Jobs bzw. Namen (Standard: 5)")
    parser.add_argument("--out", type=Path, default=Path("ai_output"), help="Ausgabeordner (Standard: ai_output/)")
    args = parser.parse_args()

    client, model = make_client()
    senders, rejected = create_senders(client, model, args.count, load_data("senders"))
    jobs, rejected_jobs = create_jobs(client, model, args.count, load_data("jobs"))
    for reason in rejected + rejected_jobs:
        print(reason if reason.startswith("korrigiert:") else f"verworfen: {reason}")
    if missing := missing_sections(senders, jobs):
        raise SystemExit(f"Nichts geschrieben: keine gültigen Einträge für {', '.join(missing)}. Bitte erneut starten.")

    data_dir = files("pdf_creator") / "data"
    texts = {
        "senders": build_senders((data_dir / "senders.toml").read_text("utf-8"), senders),
        "jobs": build_jobs((data_dir / "jobs.toml").read_text("utf-8"), jobs),
    }
    for name, text in texts.items():  # check both files before writing any of them
        try:
            tomllib.loads(text)
            text.encode("utf-8")
        except (tomllib.TOMLDecodeError, UnicodeEncodeError) as error:
            raise SystemExit(f"Nichts geschrieben: {name}.toml wäre ungültig ({error}). Bitte erneut starten.")
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (args.out / f"{name}.toml").write_text(text, encoding="utf-8", newline="\n")
    print(f"{args.out}/senders.toml und jobs.toml geschrieben ({model}): {len(jobs['jobs'])} Jobs, "
          f"{len(senders['surnames'])} Nachnamen, {len(senders['towns'])} Orte. "
          "Bitte prüfen und nach src/pdf_creator/data/ kopieren.")


if __name__ == "__main__":
    main()
