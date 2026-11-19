"""CLI: python -m pdf_creator --count 5 --type both --seed 42 --out output/"""

import argparse
import random
from pathlib import Path

from pdf_creator.generator import generate, generate_pairs
from pdf_creator.render import render

KINDS = {"angebot": ("angebot",), "rechnung": ("rechnung",), "both": ("angebot", "rechnung")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Erzeugt fiktive Muster-Angebote und -Rechnungen als PDF.")
    parser.add_argument("--count", type=int, default=5, help="Anzahl Dokumente pro Typ (Standard: 5)")
    parser.add_argument("--type", choices=KINDS, default="both", help="Dokumenttyp (Standard: both)")
    parser.add_argument("--seed", type=int, help="Seed für reproduzierbare Ausgabe (Standard: zufällig)")
    parser.add_argument("--out", type=Path, default=Path("output"), help="Ausgabeordner (Standard: output/)")
    parser.add_argument(
        "--pairs", type=int, metavar="N",
        help="Stattdessen N zusammengehörige Paare (Angebot + Rechnung) in <out>/paare/ erzeugen",
    )
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(2**32)
    print(f"Seed: {seed}")
    if args.pairs:
        for pair in generate_pairs(seed, args.pairs):
            for doc in pair:
                print(render(doc, args.out / "paare"))
        return
    for doc in generate(seed, args.count, KINDS[args.type]):
        print(render(doc, args.out))


if __name__ == "__main__":
    main()
