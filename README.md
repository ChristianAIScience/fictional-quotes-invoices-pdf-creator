# fictional-quotes-invoices-pdf-creator

Erzeugt **fiktive Muster-Angebote und -Rechnungen** als PDF. Die Dokumente dienen als **Testdaten für ein
Dokumentklassifikationssystem** (separates Projekt).

Alle Absender sind erfundene Handwerksbetriebe (Schreinerei/Tischlerei), Empfänger ist immer die
MusterAI GmbH, Lange Str. 2, 96052 Bamberg. Jedes PDF trägt das Wasserzeichen „MUSTER“ und die Fußzeile
„Fiktives Musterdokument – keine echte Rechnung / kein echtes Angebot“.

## Installation

Voraussetzung: Python ≥ 3.14 und [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

## Verwendung

```sh
# Einzelne Angebote und Rechnungen -> output/
uv run python -m pdf_creator --count 5 --type both --seed 42 --out output

# Zusammengehörige Paare (Angebot + Rechnung) -> output/paare/
uv run python -m pdf_creator --pairs 10 --seed 42 --out output

# Ground-Truth-Testset: PDF + JSON je Dokument (Ausgabeordner muss leer sein)
uv run python -m pdf_creator.testset --pairs 50 --seed 42 --out testset
```

Im Testset weichen 30 % der Rechnungen in genau einem Punkt von ihrem Angebot ab (Preis, Menge, fehlende
oder zusätzliche Position). Die JSON-Datei enthält die korrekten Werte und die Abweichung unter `deviations`.

Optional: neue Absender- und Auftragsdaten per OpenAI erzeugen (`.env` nach Vorlage `.env.example` anlegen):

```sh
uv sync --extra ai
uv run python -m pdf_creator.ai --count 5   # -> ai_output/senders.toml, ai_output/jobs.toml
```

Die Dateien in `ai_output/` prüfen und bei Bedarf manuell nach `src/pdf_creator/data/` kopieren.

## Tests

```sh
uv run python -m pytest
```

## Einschränkungen

Die erzeugten Daten sind noch **sehr einheitlich**:

- nur ein Gewerk (Schreinerei/Tischlerei) mit fünf Auftragsarten,
- ein einziges Layout für alle Absender,
- immer derselbe Empfänger, immer einseitig.

Für ein robustes Training bzw. eine aussagekräftige Evaluation des Klassifikationssystems müssen
**ergänzende Muster** erstellt werden, z. B. weitere Gewerke, unterschiedliche Layouts und Formulierungen
sowie mehrseitige Dokumente.
