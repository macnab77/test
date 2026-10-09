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
    r"(?P<pre>[-+−–—])?\s?(?P<num>\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})"
    r"(?:\s?(?:EUR|€))?(?P<post>\s?[-+−–—]|\s[SH]\b)?"
)
NUM_DATE_RE = re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4}|\d{2})?")
# "01 Okt. 2024", "2. Mai", "15. März 2026"
TEXT_DATE_RE = re.compile(
    r"^(?P<d>\d{1,2})\.?\s+(?P<mon>[A-Za-zÄÖÜäöü]{3,9})\.?(?:\s+(?P<y>20\d{2}))?(?=[\s,·•]|$)"
)
# Zweites Datum direkt dahinter (Buchungstag + Wertstellung), auch "01.03./01.03."
SECOND_DATE_RE = re.compile(r"^\s*/?\s*\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})?\s*")
YEAR_HINT_RE = re.compile(r"\b(20\d{2})\b")

MONTHS_DE = {
    "jan": 1, "januar": 1, "jänner": 1, "jän": 1, "feb": 2, "februar": 2,
    "mär": 3, "mrz": 3, "märz": 3, "maerz": 3, "mar": 3, "apr": 4, "april": 4,
    "mai": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "okt": 10, "oktober": 10,
    "nov": 11, "november": 11, "dez": 12, "dezember": 12,
}

# Zeilen, die zwar ein Datum und einen Betrag haben, aber keine Buchung sind
SKIP_WORDS = (
    "kontostand", "alter saldo", "neuer saldo", "saldo vom", "anfangssaldo",
    "endsaldo", "übertrag", "uebertrag", "summe", "gesamtbetrag", "rechnungsbetrag",
    "kreditrahmen", "verfügungsrahmen", "mindestbetrag",
)
START_BALANCE_WORDS = ("alter saldo", "anfangssaldo", "alter kontostand", "saldo vom", "übertrag", "uebertrag")

# Profile: Wie liest man die Vorzeichen in diesem Auszug?
#   bank        - Girokonto: Vorzeichen stehen da, ohne Vorzeichen = Gutschrift
#   creditcard  - Kreditkarte: Umsätze oft OHNE Vorzeichen = Ausgabe
#   traderepublic - Eingang/Ausgang in getrennten Spalten, Saldo dahinter
PROFILES = ("bank", "creditcard", "traderepublic")

INCOME_WORDS = (
    "gutschrift", "rückerstattung", "rueckerstattung", "erstattung", "zahlungseingang",
    "einzahlung", "zinszahlung", "zinsen", "dividende", "ausschüttung", "verkauf",
    "zahlung erhalten", "geld erhalten", "gehalt", "lohn", "eingang",
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
        self.description = re.sub(r"\s+", " ", self.description).strip(" -·•|")
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


def _sign(m: re.Match) -> int:
    """-1 / +1 bei ausdrücklichem Vorzeichen, 0 wenn keins dasteht."""
    pre = (m.group("pre") or "").strip()
    post = (m.group("post") or "").strip()
    # OCR liest Minuszeichen gern als Gedankenstrich
    if pre in ("-", "−", "–", "—") or post in ("-", "−", "–", "—", "S"):
        return -1
    if pre == "+" or post in ("+", "H"):
        return 1
    return 0


def _abs_amount(m: re.Match) -> float:
    return float(m.group("num").replace(".", "").replace(",", "."))


def _amount_from_match(m: re.Match) -> float:
    return -_abs_amount(m) if _sign(m) < 0 else _abs_amount(m)


def guess_year(text: str, fallback: int | None = None) -> int:
    """Häufigste Jahreszahl im Dokument (für Datumsangaben ohne Jahr)."""
    years = YEAR_HINT_RE.findall(text)
    if years:
        return int(max(set(years), key=years.count))
    return fallback or date.today().year


def match_date(line: str, default_year: int, ref: date | None = None) -> tuple[str, int] | None:
    """Datum am Zeilenanfang -> (ISO-Datum, Ende des Treffers) oder None.

    Ohne Jahresangabe gilt default_year; ist ref gesetzt (z. B. Aufnahmedatum
    eines Screenshots), wird ein Datum nach ref ins Vorjahr gelegt.
    """
    m = NUM_DATE_RE.match(line)
    if m:
        d, mth, y = int(m.group("d")), int(m.group("m")), m.group("y")
    else:
        m = TEXT_DATE_RE.match(line)
        if not m:
            return None
        mon = m.group("mon").lower().rstrip(".")
        if mon not in MONTHS_DE:
            return None
        d, mth, y = int(m.group("d")), MONTHS_DE[mon], m.group("y")
    year = (int(y) + 2000 if len(y) == 2 else int(y)) if y else (ref.year if ref else default_year)
    try:
        result = date(year, mth, d)
    except ValueError:
        return None
    if not y and ref and result > ref:
        result = result.replace(year=year - 1)
    return result.isoformat(), m.end()


def _join_split_years(text: str) -> str:
    """Trade Republic setzt das Jahr unter Tag+Monat:

        01 Okt. Kartentransaktion REWE 23,45 € 976,55 €
        2024

    -> "01 Okt. 2024 Kartentransaktion ..." (Jahr ganz vorne nach dem Datum).
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        m = TEXT_DATE_RE.match(line.strip())
        ym = re.match(r"^(20\d{2})\b\s*(.*)$", nxt)
        if m and not m.group("y") and ym and m.group("mon").lower().rstrip(".") in MONTHS_DE:
            s = line.strip()
            line = f"{s[:m.end()]} {ym.group(1)}{s[m.end():]}"
            if ym.group(2):
                line += " " + ym.group(2)
            i += 1
        out.append(line)
        i += 1
    return "\n".join(out)


@dataclass
class _Row:
    tx: Transaction
    abs_amount: float
    sign: int                 # ausdrückliches Vorzeichen (0 = keins)
    balance: float | None     # laufender Saldo in derselben Zeile


def parse_statement_text(text: str, source: str, account: str = "",
                         profile: str = "bank") -> list[Transaction]:
    """Erkennt Buchungszeilen in Kontoauszugs-Text.

    - Eine Buchung beginnt mit einem Datum (TT.MM.JJJJ, TT.MM., "01 Okt. 2024" …).
    - Am Zeilenende steht der Betrag. Stehen dort zwei Beträge, ist der
      zweite der laufende Saldo – aus dessen Änderung ergibt sich das
      Vorzeichen, wenn keins dasteht (Trade Republic).
    - Folgezeilen ohne Datum gehören zum Verwendungszweck der Buchung davor.
    """
    text = _join_split_years(text)
    year = guess_year(text)
    rows: list[_Row] = []
    current: _Row | None = None
    extra_lines = 0
    start_balance: float | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        dm = match_date(line, year)
        amounts = list(AMOUNT_RE.finditer(line))
        skip = any(w in low for w in SKIP_WORDS)
        if skip and amounts and not rows and any(w in low for w in START_BALANCE_WORDS):
            start_balance = _amount_from_match(amounts[-1])
        if dm and amounts and not skip:
            iso, end = dm
            rest = SECOND_DATE_RE.sub("", line[end:], count=1)
            pair = len(amounts) >= 2 and _trailing_pair(line, amounts)
            amount_match = amounts[-2] if pair else amounts[-1]
            balance = _amount_from_match(amounts[-1]) if pair else None
            cut = rest.find(amount_match.group(0).strip())
            desc = AMOUNT_RE.sub("", rest[:cut] if cut > 0 else rest)
            current = _Row(Transaction(iso, 0.0, desc, source, account),
                           _abs_amount(amount_match), _sign(amount_match), balance)
            rows.append(current)
            extra_lines = 0
            continue
        # Verwendungszweck-Folgezeile
        if current is not None and not dm and extra_lines < 3 and not amounts and not skip:
            current.tx.description += " " + line
            extra_lines += 1
        elif dm or amounts:
            current = None

    _resolve_signs(rows, profile, start_balance)
    return [r.tx.finalize() for r in rows]


def _resolve_signs(rows: list[_Row], profile: str, start_balance: float | None) -> None:
    # Kreditkarte: Stehen bei den meisten Umsätzen keine Minuszeichen,
    # sind die vorzeichenlosen Beträge Ausgaben und die markierten Gutschriften.
    unsigned_is_expense = profile in ("creditcard", "traderepublic")
    if profile == "creditcard":
        negatives = sum(1 for r in rows if r.sign < 0)
        cc_inverted = negatives <= len(rows) / 2
    else:
        cc_inverted = False

    prev = start_balance
    for r in rows:
        amount: float | None = None
        if cc_inverted:
            amount = r.abs_amount if r.sign != 0 or _looks_like_income(r.tx.description) else -r.abs_amount
        elif r.sign:
            amount = r.sign * r.abs_amount
        elif r.balance is not None and prev is not None:
            if abs(prev - r.abs_amount - r.balance) < 0.005:
                amount = -r.abs_amount
            elif abs(prev + r.abs_amount - r.balance) < 0.005:
                amount = r.abs_amount
        if amount is None:
            if unsigned_is_expense:
                amount = r.abs_amount if _looks_like_income(r.tx.description) else -r.abs_amount
            else:
                amount = r.abs_amount
        r.tx.amount = amount
        if r.balance is not None:
            prev = r.balance
        elif prev is not None:
            prev = round(prev + amount, 2)


def _looks_like_income(desc: str) -> bool:
    low = desc.lower()
    return any(w in low for w in INCOME_WORDS)


def _trailing_pair(line: str, amounts: list[re.Match]) -> bool:
    """True, wenn die letzten beiden Beträge direkt nebeneinander am Zeilenende stehen."""
    a, b = amounts[-2], amounts[-1]
    between = line[a.end(): b.start()]
    return between.strip() == "" and line[b.end():].strip() == ""


# --- PayPal-Screenshots (App / Web "Aktivitäten") ----------------------------

PAYPAL_SCREEN_SKIP = (
    "bankkonto", "von bank", "auf bank", "bankgutschrift", "überweisung auf",
    "abbuchung von", "währungsumrechnung", "ausstehend", "storniert", "abgelehnt",
)


def parse_paypal_screenshot(text: str, source: str, ref: date | None = None) -> list[Transaction]:
    """PayPal-Aktivitätenliste aus OCR-Text.

    Ein Eintrag sieht in der App etwa so aus (Reihenfolge variiert):

        Spotify AB                    -10,99 €
        2. Mai · Automatische Zahlung

    Jede Zeile mit einem Euro-Betrag ist ein Eintrag. Name = Text vor dem
    Betrag (oder Zeile davor), Datum = nächstes Datum in den umliegenden Zeilen.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    default_year = (ref.year if ref else guess_year(text))
    out: list[Transaction] = []
    used_dates: set[int] = set()
    for i, line in enumerate(lines):
        m = None
        for cand in AMOUNT_RE.finditer(line):
            if "€" in cand.group(0) or "EUR" in cand.group(0) or _sign(cand):
                m = cand
        if not m:
            continue
        name = line[: m.start()].strip(" ·•-")
        if not name and i > 0 and not AMOUNT_RE.search(lines[i - 1]) and not match_date(lines[i - 1], default_year, ref):
            name = lines[i - 1]
        iso = None
        context = ""
        for j in (i, i + 1, i - 1, i + 2):
            if 0 <= j < len(lines) and j not in used_dates:
                dm = _find_date(lines[j], default_year, ref)
                if dm:
                    iso = dm
                    used_dates.add(j)
                    context = lines[j] if j != i else ""
                    break
        if not iso:
            continue
        # Datum vorne abschneiden ("2. Mai · Zahlung" -> "Zahlung"; OCR macht aus · oft -)
        detail = re.sub(r"^\d{1,2}\.\s*(?:[A-Za-zÄÖÜäöü]{3,9}\.?|\d{1,2}\.)(?:\s*20\d{2})?\s*[·•\-–—]?\s*", "", context)
        desc = " · ".join(p for p in (name, detail) if p)
        if any(w in desc.lower() for w in PAYPAL_SCREEN_SKIP):
            continue
        sign = _sign(m)
        amount = _abs_amount(m) if sign > 0 else -_abs_amount(m)
        out.append(Transaction(iso, amount, desc or "PayPal", source, "PayPal").finalize())
    return out


def _find_date(line: str, default_year: int, ref: date | None) -> str | None:
    """Datum irgendwo in der Zeile (PayPal schreibt es nicht immer an den Anfang)."""
    for m in re.finditer(r"\d{1,2}\.", line):
        dm = match_date(line[m.start():], default_year, ref)
        if dm:
            return dm[0]
    return None


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
    (Postbank -> PayPal, Postbank -> Trade Republic, Kreditkartenabrechnung).
    Die App zählt es weder als Einnahme noch als Ausgabe, weil die
    eigentlichen Käufe schon im jeweils anderen Konto stehen.
    """
    text = tx.description.lower()
    account = tx.account.lower()
    for category, keywords in rules.items():
        for k in keywords:
            k = k.lower()
            # "paypal" im PayPal-Konto selbst ist keine Umbuchung, "trade republic"
            # im Trade-Republic-Auszug auch nicht – nur auf dem jeweils anderen Konto.
            if category == "Umbuchung" and account and (k in account or account.split()[0] in k):
                continue
            if k in text:
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
