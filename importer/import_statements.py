#!/usr/bin/env python3
"""Liest einen Ordner mit Kontoauszügen und PayPal-Umsätzen ein.

Unterstützt:
  *.pdf                 Kontoauszüge / PayPal-Auszüge mit Textebene (pdfplumber),
                        eingescannte PDFs per OCR (falls Tesseract installiert)
  *.png *.jpg *.jpeg    Screenshots per OCR (Tesseract, Sprache Deutsch)
  *.csv                 PayPal-Export (Aktivitäten -> Kontoauszüge -> CSV)

Ausgabe: transactions.json (für die iPhone-App) und transactions.csv.

Beispiel:
  python3 importer/import_statements.py ~/Finanzen/Auszuege -o transactions.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse import (  # noqa: E402
    Transaction, categorize, dedupe, parse_paypal_csv, parse_statement_text,
)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".webp"}


def ocr_image(path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise RuntimeError("Für Screenshots bitte installieren: pip install pytesseract pillow "
                           "(und Tesseract selbst, z. B. 'brew install tesseract tesseract-lang')")
    if path.suffix.lower() == ".heic":
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            raise RuntimeError("Für HEIC-Bilder: pip install pillow-heif")
    img = Image.open(path)
    # --psm 6: ein einheitlicher Textblock – hält Tabellenzeilen zusammen
    return pytesseract.image_to_string(img, lang="deu", config="--psm 6")


def pdf_text(path: Path) -> str:
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(layout=False) or ""
            if not text.strip():
                # Eingescannte Seite -> OCR
                try:
                    import pytesseract
                    img = page.to_image(resolution=300).original
                    text = pytesseract.image_to_string(img, lang="deu", config="--psm 6")
                except ImportError:
                    print(f"  ! {path.name} S.{page.page_number}: kein Text, OCR nicht installiert",
                          file=sys.stderr)
            pages.append(text)
    return "\n".join(pages)


def account_for(path: Path, text: str) -> str:
    name = path.name.lower()
    if "paypal" in name or "paypal" in text[:3000].lower():
        return "PayPal"
    return path.parent.name if path.parent.name else "Girokonto"


def read_file(path: Path, debug: bool) -> list[Transaction]:
    ext = path.suffix.lower()
    if ext == ".csv":
        return parse_paypal_csv(path.read_text(encoding="utf-8-sig", errors="replace"), path.name)
    if ext == ".pdf":
        text = pdf_text(path)
    elif ext in IMAGE_EXT:
        text = ocr_image(path)
    else:
        return []
    if debug:
        print(f"----- {path.name} -----\n{text}\n", file=sys.stderr)
    return parse_statement_text(text, path.name, account_for(path, text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="Ordner mit PDFs, Screenshots und CSVs")
    ap.add_argument("-o", "--output", type=Path, default=Path("transactions.json"))
    ap.add_argument("--rules", type=Path, default=Path(__file__).with_name("categories.json"),
                    help="Kategorie-Regeln (Kategorie -> Suchbegriffe)")
    ap.add_argument("--debug", action="store_true", help="Erkannten Rohtext ausgeben")
    args = ap.parse_args()

    if not args.folder.is_dir():
        ap.error(f"Ordner nicht gefunden: {args.folder}")
    rules = json.loads(args.rules.read_text(encoding="utf-8"))

    all_tx: list[Transaction] = []
    files = sorted(p for p in args.folder.rglob("*") if p.is_file() and not p.name.startswith("."))
    for path in files:
        try:
            txs = read_file(path, args.debug)
        except Exception as e:  # eine kaputte Datei soll den Rest nicht stoppen
            print(f"  ! {path.name}: {e}", file=sys.stderr)
            continue
        if txs or path.suffix.lower() in {".pdf", ".csv", *IMAGE_EXT}:
            flag = "" if txs else "   <- nichts erkannt, mit --debug prüfen"
            print(f"  {len(txs):4d} Buchungen  {path.name}{flag}")
        all_tx.extend(txs)

    for t in all_tx:
        t.category = categorize(t, rules)
    all_tx = dedupe(all_tx)

    payload = {
        "exported": datetime.now().isoformat(timespec="seconds"),
        "transactions": [t.to_dict() for t in all_tx],
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    with args.output.with_suffix(".csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Datum", "Betrag", "Beschreibung", "Kategorie", "Konto", "Quelle"])
        for t in all_tx:
            w.writerow([t.date, f"{t.amount:.2f}".replace(".", ","), t.description,
                        t.category, t.account, t.source])

    income = sum(t.amount for t in all_tx if t.amount > 0)
    expense = sum(t.amount for t in all_tx if t.amount < 0)
    print(f"\n{len(all_tx)} Buchungen -> {args.output}")
    print(f"Einnahmen {income:,.2f} €   Ausgaben {expense:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
