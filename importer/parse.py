"""Parser für Kontoauszüge (Text aus PDF/OCR) und PayPal-CSV-Exporte.

Alle Funktionen arbeiten auf Text bzw. Zeilen, damit sie ohne echte
Dateien testbar sind. Das Einlesen der Dateien passiert in
import_statements.py.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, asdict
from datetime import date

# 1.234,56 | 1234,56 | 12,34 – optional mit Vorzeichen davor oder dahinter,
# optional mit S/H (Soll/Haben) oder EUR/€ dahinter.
AMOUNT_RE = re.compile(
    r"(?P<pre>[-+−])?\s?(?P<num>\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})"
    r"(?:\s?(?:EUR|€))?(?P<post>\s?[-+−]|\s[SH]\b)?"
)
DATE_RE = re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4}|\d{2})?")
# Zweites Datum direkt dahinter (Buchungstag + Wertstellung)
SECOND_DATE_RE = re.compile(r"^\s*\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})?\s+")
YEAR_HINT_RE = re.compile(r"\b(20\d{2})\b")

# Zeilen, die zwar ein Datum und einen Betrag haben, aber keine Buchung sind
SKIP_WORDS = (
    "kontostand", "alter saldo", "neuer saldo", "saldo vom", "anfangssaldo",
    "endsaldo", "übertrag", "uebertrag", "summe",
)


@dataclass
class Transaction:
    date: str          # ISO yyyy-mm-dd
    amount: float      # positiv = Einnahme, negativ = Ausgabe
    description: str
    source: str        # Dateiname
    account: str = ""
    category: str = ""
    id: str = ""

    def finalize(self) -> "Transaction":
        self.description = re.sub(r"\s+", " ", self.description).strip()
        if not self.id:
            key = f"{self.date}|{self.amount:.2f}|{self.description.lower()[:40]}"
            self.id = hashlib.sha1(key.encode()).hexdigest()[:12]
        return self

    def to_dict(self) -> dict:
        return asdict(self)


def parse_amount(text: str) -> float | None:
    """'1.234,56-' -> -1234.56, '+12,00' -> 12.0, '5,00 S' -> -5.0"""
    m = AMOUNT_RE.search(text)
    if not m:
        return None
    return _amount_from_match(m)


def _amount_from_match(m: re.Match) -> float:
    value = float(m.group("num").replace(".", "").replace(",", "."))
    pre = (m.group("pre") or "").strip()
    post = (m.group("post") or "").strip()
    if pre in ("-", "−") or post in ("-", "−", "S"):
        value = -value
    return value


def guess_year(text: str, fallback: int | None = None) -> int:
    """Häufigste Jahreszahl im Dokument (für Datumsangaben ohne Jahr)."""
    years = YEAR_HINT_RE.findall(text)
    if years:
        return int(max(set(years), key=years.count))
    return fallback or date.today().year


def _parse_date(m: re.Match, default_year: int) -> str | None:
    d, mth, y = int(m.group("d")), int(m.group("m")), m.group("y")
    year = default_year if not y else (int(y) + 2000 if len(y) == 2 else int(y))
    try:
        return date(year, mth, d).isoformat()
    except ValueError:
        return None


def parse_statement_text(text: str, source: str, account: str = "") -> list[Transaction]:
    """Erkennt Buchungszeilen in Kontoauszugs-Text.

    Heuristik (funktioniert für die meisten deutschen Banken):
    - Eine Buchung beginnt mit einem Datum (TT.MM.JJJJ, TT.MM.JJ oder TT.MM.).
    - Am Zeilenende steht der Betrag. Stehen dort zwei Beträge, ist der
      zweite meist der laufende Kontostand und wird ignoriert.
    - Folgezeilen ohne Datum gehören zum Verwendungszweck der Buchung davor.
    """
    year = guess_year(text)
    result: list[Transaction] = []
    current: Transaction | None = None
    extra_lines = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        dm = DATE_RE.match(line)
        amounts = list(AMOUNT_RE.finditer(line))
        if dm and amounts and not any(w in line.lower() for w in SKIP_WORDS):
            iso = _parse_date(dm, year)
            if iso:
                rest = line[dm.end():]
                rest = SECOND_DATE_RE.sub("", rest, count=1)
                # Betrag = erster Betrag der letzten "Betragsgruppe" am Ende
                amount_match = amounts[-2] if len(amounts) >= 2 and _trailing_pair(line, amounts) else amounts[-1]
                desc = rest[: max(0, rest.find(amount_match.group(0).strip()))] or rest
                desc = AMOUNT_RE.sub("", desc)
                current = Transaction(iso, _amount_from_match(amount_match), desc, source, account)
                result.append(current)
                extra_lines = 0
                continue
        # Verwendungszweck-Folgezeile
        if current is not None and not dm and extra_lines < 3 and not amounts:
            if not any(w in line.lower() for w in SKIP_WORDS):
                current.description += " " + line
                extra_lines += 1
        elif dm or amounts:
            current = None

    return [t.finalize() for t in result]


def _trailing_pair(line: str, amounts: list[re.Match]) -> bool:
    """True, wenn die letzten beiden Beträge direkt nebeneinander am Zeilenende stehen."""
    a, b = amounts[-2], amounts[-1]
    between = line[a.end(): b.start()]
    return between.strip() == "" and line[b.end():].strip() == ""


# --- PayPal ----------------------------------------------------------------

PAYPAL_SKIP_TYPES = (
    # Interne Umbuchungen, die sonst doppelt gezählt würden
    "bankgutschrift auf paypal-konto", "allgemeine abbuchung",
    "abbuchung von kreditkarte", "allgemeine währungsumrechnung",
    "general currency conversion", "bank deposit to pp account",
    "user initiated withdrawal", "general card deposit",
    "autorisierung", "authorization",
)


def _col(row: dict, *names: str) -> str:
    for n in names:
        for key in row:
            if key and key.strip().strip('"﻿').lower() == n:
                return (row[key] or "").strip()
    return ""


def parse_paypal_csv(content: str, source: str) -> list[Transaction]:
    """Liest den PayPal-Export (Aktivitäten -> Kontoauszüge -> CSV).

    Unterstützt deutsche und englische Spaltennamen.
    """
    sample = content[:2000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")), delimiter=delimiter)
    out: list[Transaction] = []
    for row in reader:
        status = _col(row, "status").lower()
        if status and status not in ("abgeschlossen", "completed"):
            continue
        typ = _col(row, "typ", "type", "beschreibung", "description")
        if typ.lower() in PAYPAL_SKIP_TYPES:
            continue
        raw_date = _col(row, "datum", "date")
        amount_txt = _col(row, "netto", "net", "brutto", "gross")
        if not raw_date or not amount_txt:
            continue
        iso = _paypal_date(raw_date)
        amount = _paypal_amount(amount_txt)
        if iso is None or amount is None:
            continue
        name = _col(row, "name")
        subject = _col(row, "artikelbezeichnung", "item title", "betreff", "subject", "hinweis", "note")
        desc = " – ".join(p for p in (name, subject or typ) if p)
        out.append(Transaction(iso, amount, desc or typ, source, "PayPal").finalize())
    return out


def _paypal_date(s: str) -> str | None:
    for pat in (r"(\d{1,2})\.(\d{1,2})\.(\d{4})", r"(\d{1,2})/(\d{1,2})/(\d{4})"):
        m = re.match(pat, s)
        if m:
            a, b, y = (int(x) for x in m.groups())
            # Englischer Export: MM/DD/YYYY
            d, mth = (b, a) if "/" in s else (a, b)
            try:
                return date(y, mth, d).isoformat()
            except ValueError:
                return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return s[:10] if m else None


def _paypal_amount(s: str) -> float | None:
    s = s.replace("\xa0", "").replace(" ", "")
    if re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})*,\d{2}|[-+]?\d+,\d{2}", s):
        return float(s.replace(".", "").replace(",", "."))
    if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})*\.\d{2}|[-+]?\d+\.\d{2}", s):
        return float(s.replace(",", ""))
    return None


# --- Kategorien ---------------------------------------------------------------

def categorize(tx: Transaction, rules: dict[str, list[str]]) -> str:
    """Erste passende Regel gewinnt (Reihenfolge in categories.json).

    "Umbuchung" markiert Geld, das nur zwischen eigenen Konten wandert
    (z. B. Girokonto -> PayPal). Die App zählt es nicht als Ausgabe, weil
    der eigentliche Einkauf schon im PayPal-Export steht.
    """
    text = tx.description.lower()
    for category, keywords in rules.items():
        if category == "Umbuchung" and tx.account == "PayPal":
            continue
        if any(k.lower() in text for k in keywords):
            return category
    return "Gehalt & Einnahmen" if tx.amount > 0 and "Gehalt & Einnahmen" in rules else "Sonstiges"


def dedupe(transactions: list[Transaction]) -> list[Transaction]:
    """Entfernt Buchungen, die in mehreren Dateien vorkommen (z. B. PDF und Screenshot).

    Zwei gleiche Buchungen *in derselben Datei* (zweimal 3,50 € beim Bäcker)
    bleiben erhalten: Die n-te Wiederholung innerhalb einer Datei bekommt
    eine eigene ID.
    """
    seen: dict[str, Transaction] = {}
    per_source: dict[tuple[str, str], int] = {}
    for t in transactions:
        n = per_source.get((t.source, t.id), 0)
        per_source[(t.source, t.id)] = n + 1
        key = t.id if n == 0 else f"{t.id}-{n}"
        if key not in seen:
            t.id = key
            seen[key] = t
    return sorted(seen.values(), key=lambda t: (t.date, t.description))
