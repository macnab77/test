#!/usr/bin/env python3
"""Anonymisiert Kontoauszüge (PDF), Screenshots und CSVs – lokal, nichts verlässt den Rechner.

Ersetzt durch erfundene, aber gleich aussehende Werte:
  automatisch   IBANs, Gläubiger-IDs, Kartennummern (auch maskierte), Kunden-,
                Konto-, Mandats- und Referenznummern, lange Ziffernfolgen,
                E-Mail-Adressen, Telefonnummern
  auf Nachfrage dein Name, deine Adresse, weitere Namen (Partner, Vermieter …)

Datum, Beträge und Händlernamen bleiben erhalten, damit der Importer
weiter getestet werden kann. Gleiche Originalwerte bekommen in allen
Dateien denselben Ersatz.

Danach prüft das Skript jede Datei: Steht noch irgendwo ein Originalwert
drin, wird das gemeldet.

Beispiel:
  python3 importer/anonymize.py ~/Finanzen
  -> schreibt ~/Finanzen_anonym/ mit derselben Ordnerstruktur
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".webp"}

# Beträge dürfen nie verändert werden (sonst stimmt das Parsen nicht mehr)
PROTECTED_RE = re.compile(r"[-+−]?\s?\d{1,3}(?:\.\d{3})*,\d{2}(?!\d)|[-+−]?\s?\d+,\d{2}(?!\d)")

FAKE_NAMES = [
    ("Max", "Mustermann"), ("Erika", "Musterfrau"), ("Hans", "Beispiel"),
    ("Anna", "Probe"), ("Paul", "Platzhalter"), ("Lena", "Exempel"),
    ("Tom", "Testmann"), ("Mia", "Muster"),
]

# Bezeichnungen, hinter denen eine persönliche Nummer steht
ID_LABELS = (
    r"Kunden-?\s?(?:nummer|nr\.?)|Konto-?\s?(?:nummer|nr\.?)|Karten-?\s?(?:nummer|nr\.?)|"
    r"Depot-?\s?(?:nummer|nr\.?)|Vertrags-?\s?(?:nummer|nr\.?)|Rechnungs-?\s?(?:nummer|nr\.?)|"
    r"Mandatsreferenz|Mandatsref\.?|Mandat|Referenz|Ref\.?|End-to-End-Ref\.?|EREF|MREF|KREF|"
    r"Steuer-?\s?ID|Steuernummer|Transaktionscode|Transaktions-?ID|Kundenreferenz"
)

DETECTORS: list[tuple[str, re.Pattern]] = [
    # IBAN: Länderkennung, Prüfziffer, 4er-Blöcke (mit oder ohne Leerzeichen)
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")),
    ("glaeubiger", re.compile(r"\b[A-Z]{2}\d{2}ZZZ[A-Z0-9]{8,11}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("phone", re.compile(r"(?<![\w+])\+\d{2}[ /-]?\(?0?\)?\d{2,4}[ /-]?\d{3,}(?:[ -]?\d+)*")),
    # Kartennummern, auch maskiert: 4111 **** **** 1234, 4111XXXXXXXX1234
    ("card", re.compile(r"\b\d{4}[ -]?(?:[\d*Xx•]{4}[ -]?){2}[\d*Xx•]{1,4}\d{0,3}\b")),
    ("labeled_id", re.compile(rf"(?:{ID_LABELS})\s*[:.]?\s*(?P<v>[A-Z0-9][A-Z0-9/.\-]{{3,}})", re.I)),
    # 6+ Ziffern am Stück, aber keine Beträge (1234567,89)
    ("digits", re.compile(r"(?<![\d,.])\d{6,}(?![\d]|[,.]\d)")),
]


@dataclass
class Span:
    start: int
    end: int
    original: str
    fake: str
    literal: bool = False   # aus deinen persönlichen Angaben (nicht automatisch erkannt)


@dataclass
class Anonymizer:
    seed: int | None = None
    mapping: dict[str, str] = field(default_factory=dict)   # normalisiert -> Ersatz
    originals: dict[str, str] = field(default_factory=dict) # normalisiert -> Original (für Prüfung)
    literals: list[tuple[re.Pattern, str]] = field(default_factory=list)

    def __post_init__(self):
        self.rng = random.Random(self.seed)
        self._names = list(FAKE_NAMES)

    # --- persönliche Angaben ------------------------------------------------
    def add_name(self, full: str) -> None:
        parts = [p for p in re.split(r"[\s,]+", full.strip()) if len(p) >= 3]
        if not parts:
            return
        first, last = self._next_fake_name(parts)
        fake_parts = [first] + [last] * (len(parts) - 1) if len(parts) > 1 else [first]
        if len(parts) > 1:
            self._add_literal(full, f"{first} {last}")
            self._add_literal(f"{parts[-1]}, {' '.join(parts[:-1])}", f"{last}, {first}")
        for p, f in zip(parts, fake_parts):
            self._add_literal(p, f)

    def add_street(self, street: str) -> None:
        if street.strip():
            m = re.search(r"\d+\s*[a-zA-Z]?$", street.strip())
            self._add_literal(street, "Beispielweg " + (str(self.rng.randint(1, 40)) if m else "1"))
            name_only = re.sub(r"\s*\d+\s*[a-zA-Z]?$", "", street.strip())
            if len(name_only) >= 5:
                self._add_literal(name_only, "Beispielweg")

    def add_city(self, plz_city: str) -> None:
        s = plz_city.strip()
        if not s:
            return
        m = re.match(r"^(?:[A-Z]-)?(\d{4,5})\s+(.+)$", s)
        if m:
            fake_plz = "1" + "".join(str(self.rng.randint(0, 9)) for _ in range(len(m.group(1)) - 1))
            self._add_literal(s, f"{fake_plz} Musterstadt")
            self._add_literal(m.group(1), fake_plz)
        else:
            self._add_literal(s, "Musterstadt")

    def add_text(self, text: str, fake: str | None = None) -> None:
        if text.strip():
            self._add_literal(text, fake or self._fake_like(text))

    def _next_fake_name(self, real_parts: list[str]) -> tuple[str, str]:
        real = {p.lower() for p in real_parts}
        while self._names:
            first, last = self._names.pop(0)
            if first.lower() not in real and last.lower() not in real:
                return first, last
        n = self.rng.randint(10, 99)
        return f"Person{n}", f"Nachname{n}"

    def _add_literal(self, original: str, fake: str) -> None:
        words = [re.escape(w) for w in original.split()]
        if not words:
            return
        pattern = re.compile(r"(?<![\w])" + r"\s+".join(words) + r"(?![\w])", re.I)
        self.literals.append((pattern, fake))
        self._remember(original, fake)
        # längste zuerst, damit "Max Muster" vor "Max" greift
        self.literals.sort(key=lambda lf: -len(lf[0].pattern))

    # --- Erkennung ---------------------------------------------------------
    def spans(self, text: str) -> list[Span]:
        cands: list[Span] = []
        for pattern, fake in self.literals:
            for m in pattern.finditer(text):
                cands.append(Span(m.start(), m.end(), m.group(0), _match_case(m.group(0), fake), True))
        for kind, pattern in DETECTORS:
            for m in pattern.finditer(text):
                if kind == "labeled_id":
                    v = m.group("v")
                    if sum(c.isdigit() for c in v) < 3:
                        continue
                    s, e = m.start("v"), m.end("v")
                elif kind == "iban":
                    compact = m.group(0).replace(" ", "")
                    if not (15 <= len(compact) <= 34):
                        continue
                    s, e = m.start(), m.end()
                elif kind == "card" and sum(c.isdigit() for c in m.group(0)) < 4:
                    continue
                else:
                    s, e = m.start(), m.end()
                original = text[s:e]
                cands.append(Span(s, e, original, self._fake_for(kind, original)))
        # Automatisch erkannte Werte dürfen keinen Betrag anschneiden
        protected = [(m.start(), m.end()) for m in PROTECTED_RE.finditer(text)]
        cands = [c for c in cands if c.literal or
                 not any(c.start < e and c.end > s for s, e in protected)]
        # nicht überlappend: früher Start zuerst, bei Gleichstand der längere
        cands.sort(key=lambda sp: (sp.start, -(sp.end - sp.start)))
        out: list[Span] = []
        for sp in cands:
            if out and sp.start < out[-1].end:
                continue
            out.append(sp)
        return out

    def replace(self, text: str) -> str:
        parts, pos = [], 0
        for sp in self.spans(text):
            parts.append(text[pos:sp.start])
            parts.append(sp.fake)
            pos = sp.end
        parts.append(text[pos:])
        return "".join(parts)

    # --- Ersatzwerte -------------------------------------------------------
    def _remember(self, original: str, fake: str) -> None:
        key = _norm(original)
        self.mapping[key] = fake
        self.originals[key] = original

    def _fake_for(self, kind: str, original: str) -> str:
        key = _norm(original)
        if key in self.mapping:
            return self.mapping[key]
        if kind == "iban":
            fake = self._fake_iban(original)
        elif kind == "email":
            fake = f"{self._names[0][0].lower() if self._names else 'max'}.muster{self.rng.randint(1, 99)}@example.com"
        else:
            fake = self._fake_like(original)
        self._remember(original, fake)
        return fake

    def _fake_like(self, s: str) -> str:
        """Gleiches Muster: Ziffern -> zufällige Ziffern, Buchstaben -> zufällige Buchstaben."""
        if not any(c.isascii() and c.isalnum() for c in s):
            return "X" * max(3, len(s))
        out = []
        for i, c in enumerate(s):
            if c.isdigit():
                lo = 1 if i == 0 and c != "0" else 0
                out.append(str(self.rng.randint(lo, 9)))
            elif c.isalpha() and c.isascii():
                ch = chr(self.rng.randint(65, 90))
                out.append(ch if c.isupper() else ch.lower())
            else:
                out.append(c)
        fake = "".join(out)
        return fake if fake != s else self._fake_like(s)

    def _fake_iban(self, original: str) -> str:
        compact = original.replace(" ", "")
        cc = compact[:2]
        bban = self._fake_like(compact[4:])
        numeric = "".join(str(int(c, 36)) for c in bban + cc + "00")
        check = 98 - int(numeric) % 97
        fake = f"{cc}{check:02d}{bban}"
        # Leerzeichen wie im Original setzen
        it = iter(fake)
        return "".join(" " if c == " " else next(it) for c in original)

    def leaks(self, text: str) -> list[str]:
        """Originalwerte, die noch im Text stehen."""
        norm = _norm(text)
        fakes = {_norm(f) for f in self.mapping.values()}
        found = []
        for key, original in self.originals.items():
            if len(key) < 3 or any(key in f for f in fakes):
                continue
            if key in norm:
                found.append(original)
        return found


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _match_case(original: str, fake: str) -> str:
    return fake.upper() if original.isupper() and len(original) > 1 else fake


# --- PDF ----------------------------------------------------------------------

def anonymize_pdf(src: Path, dst: Path, anon: Anonymizer) -> list[str]:
    import pymupdf

    warnings = []
    doc = pymupdf.open(src)
    pairs: dict[str, str] = {}
    for page in doc:
        text = page.get_text()
        if not text.strip():
            warnings.append(f"Seite {page.number + 1} hat keinen Text (Scan?) – nicht anonymisiert, bitte nicht verschicken")
        for sp in anon.spans(text):
            pairs[sp.original] = sp.fake

    for page in doc:
        taken: list = []
        for original, fake in sorted(pairs.items(), key=lambda kv: -len(kv[0])):
            for rect in page.search_for(original):
                if any(rect.intersects(t) for t, _ in taken):
                    continue
                taken.append((rect, fake))
                page.add_redact_annot(rect, fill=(1, 1, 1))
        # Text unter den Schwärzungen wirklich entfernen, Bilder unangetastet lassen
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        # Ersatztext in Originalgröße auf die Grundlinie schreiben
        for rect, fake in taken:
            fs = _fit_fontsize(fake, rect)
            page.insert_text((rect.x0, rect.y1 - rect.height * 0.21), fake, fontname="helv", fontsize=fs)

    doc.set_metadata({})
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dst, garbage=4, deflate=True, clean=True)
    doc.close()

    check = pymupdf.open(dst)
    text = "\n".join(p.get_text() for p in check)
    check.close()
    leaks = anon.leaks(text)
    if leaks:
        warnings.append("NOCH ENTHALTEN: " + ", ".join(sorted(set(leaks))))
    return warnings


def _fit_fontsize(text: str, rect) -> float:
    import pymupdf
    fs = max(4.0, rect.height / 1.16)
    while fs > 4 and pymupdf.get_text_length(text, fontname="helv", fontsize=fs) > rect.width:
        fs -= 0.25
    return fs


# --- Screenshots ----------------------------------------------------------------

def anonymize_image(src: Path, dst: Path, anon: Anonymizer) -> list[str]:
    import pytesseract
    from PIL import Image, ImageDraw

    if src.suffix.lower() == ".heic":
        from pillow_heif import register_heif_opener
        register_heif_opener()
        dst = dst.with_suffix(".png")
    img = Image.open(src).convert("RGB")
    data = pytesseract.image_to_data(img, lang="deu", config="--psm 6",
                                     output_type=pytesseract.Output.DICT)

    # Wörter zu Zeilen zusammensetzen, Zeichenposition -> Wort merken
    lines: dict[tuple, list[int]] = {}
    for i, word in enumerate(data["text"]):
        if word.strip():
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append(i)

    draw = ImageDraw.Draw(img)
    for idxs in lines.values():
        text, offsets = "", []
        for i in idxs:
            if text:
                text += " "
            offsets.append((len(text), len(text) + len(data["text"][i]), i))
            text += data["text"][i]
        for sp in anon.spans(text):
            hit = [i for s, e, i in offsets if s < sp.end and e > sp.start]
            if not hit:
                continue
            x0 = min(data["left"][i] for i in hit)
            y0 = min(data["top"][i] for i in hit)
            x1 = max(data["left"][i] + data["width"][i] for i in hit)
            y1 = max(data["top"][i] + data["height"][i] for i in hit)
            _paint_replacement(img, draw, (x0 - 2, y0 - 2, x1 + 2, y1 + 2), sp.fake)

    dst.parent.mkdir(parents=True, exist_ok=True)
    # neu speichern = ohne EXIF/GPS-Metadaten
    img.save(dst, quality=95) if dst.suffix.lower() in (".jpg", ".jpeg") else img.save(dst)

    warnings = []
    leaks = anon.leaks(pytesseract.image_to_string(Image.open(dst), lang="deu", config="--psm 6"))
    if leaks:
        warnings.append("NOCH ENTHALTEN: " + ", ".join(sorted(set(leaks))))
    warnings.append("Bild bitte kurz selbst ansehen – Texterkennung kann Namen übersehen")
    return warnings


def _paint_replacement(img, draw, box, text: str) -> None:
    x0, y0, x1, y1 = (max(0, v) for v in box)
    bg = img.getpixel((max(0, x0 - 3), (y0 + y1) // 2))
    region = img.crop((x0, y0, x1, y1)).convert("L")
    lum_bg = sum(bg) / 3
    lo, hi = region.getextrema()
    ink = lo if lum_bg > 128 else hi
    draw.rectangle((x0, y0, x1, y1), fill=bg)
    size = max(8, int((y1 - y0) * 1.05))
    font = _font(size)
    while size > 8 and draw.textlength(text, font=font) > (x1 - x0) * 1.15:
        size -= 1
        font = _font(size)
    draw.text((x0 + 1, y0 + (y1 - y0 - size) / 2 - 1), text, fill=(ink, ink, ink), font=font)


_FONT_CACHE: dict[int, object] = {}


def _font(size: int):
    from PIL import ImageFont
    if size not in _FONT_CACHE:
        for path in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf",
                     "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                     "C:/Windows/Fonts/arial.ttf"):
            try:
                _FONT_CACHE[size] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            _FONT_CACHE[size] = ImageFont.load_default(size=size)
    return _FONT_CACHE[size]


# --- CSV / Text -------------------------------------------------------------------

def anonymize_text_file(src: Path, dst: Path, anon: Anonymizer) -> list[str]:
    text = src.read_text(encoding="utf-8-sig", errors="replace")
    out = anon.replace(text)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out, encoding="utf-8")
    leaks = anon.leaks(out)
    return ["NOCH ENTHALTEN: " + ", ".join(sorted(set(leaks)))] if leaks else []


# --- Ablauf ------------------------------------------------------------------------

def ask_personal_data(anon: Anonymizer, args) -> None:
    names = list(args.name or [])
    streets = list(args.strasse or [])
    cities = list(args.ort or [])
    if not args.no_input and not (names or streets or cities):
        print("Persönliche Angaben, die ersetzt werden sollen (Enter = überspringen).")
        print("Sie werden nirgends gespeichert.\n")
        names.append(input("Dein Vor- und Nachname:                 "))
        streets.append(input("Straße und Hausnummer:                  "))
        cities.append(input("PLZ und Ort (z. B. 10115 Berlin):       "))
        more = input("Weitere Namen (Partner, Vermieter, …),\nmit Komma getrennt:                     ")
        names += [n for n in more.split(",") if n.strip()]
        print()
    for n in names:
        anon.add_name(n)
    for s in streets:
        anon.add_street(s)
    for c in cities:
        anon.add_city(c)
    for t in args.text or []:
        anon.add_text(t)


def target_path(src: Path, src_root: Path, dst_root: Path, anon: Anonymizer) -> Path:
    """Zielpfad – auch Ordner- und Dateinamen werden anonymisiert
    (Kontoauszüge heißen oft "Konto_<Kontonummer>_….pdf")."""
    rel = src.relative_to(src_root)
    parts = [anon.replace(p) for p in rel.parts]
    dst = dst_root.joinpath(*parts)
    return dst.with_suffix(".png") if src.suffix.lower() == ".heic" else dst


def check_dependencies(files: list[Path]) -> list[str]:
    """Fehlende Pakete/Programme vorab melden statt bei jeder Datei."""
    import importlib.util
    import shutil
    missing = []
    exts = {f.suffix.lower() for f in files}
    need = []
    if ".pdf" in exts:
        need += ["pymupdf", "pdfplumber"]
    if exts & IMAGE_EXT:
        need += ["pytesseract", "PIL"]
    if ".heic" in exts:
        need += ["pillow_heif"]
    for mod in need:
        if importlib.util.find_spec(mod) is None:
            missing.append({"PIL": "pillow", "pillow_heif": "pillow-heif"}.get(mod, mod))
    if exts & IMAGE_EXT and not shutil.which("tesseract"):
        missing.append("tesseract (Programm)")
    return missing


INSTALL_HELP = """
Es fehlen: {missing}

Installieren in die Python-Umgebung, mit der dieses Skript gerade läuft:

    {python} -m pip install {packages}
{brew}
Läuft gerade mit: {python}
"""


def parse_check(pairs: list[tuple[Path, Path]], dst_root: Path) -> None:
    """Zählt Buchungen vor/nach dem Anonymisieren – sollte gleich bleiben."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from import_statements import read_file
    except Exception:
        return
    print("\nKontrolle: erkannte Buchungen  Original -> anonymisiert")
    for src, dst in pairs:
        name = dst.relative_to(dst_root)
        try:
            a = read_file(src, False)
            b = read_file(dst, False)
        except Exception as e:
            print(f"  {name}: übersprungen ({e})")
            continue
        sa, sb = round(sum(t.amount for t in a), 2), round(sum(t.amount for t in b), 2)
        flag = "" if (len(a), sa) == (len(b), sb) else "   <- weicht ab"
        print(f"  {len(a):4d} -> {len(b):4d}   {name}{flag}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="Ordner mit den Originalen")
    ap.add_argument("-o", "--output", type=Path, help="Zielordner (Standard: <Ordner>_anonym)")
    ap.add_argument("--name", action="append", help="Name, der ersetzt werden soll (mehrfach möglich)")
    ap.add_argument("--strasse", action="append", help="Straße + Hausnummer")
    ap.add_argument("--ort", action="append", help="PLZ + Ort")
    ap.add_argument("--text", action="append", help="Beliebiger weiterer Text, der ersetzt werden soll")
    ap.add_argument("--no-input", action="store_true", help="Nicht nachfragen")
    ap.add_argument("--no-check", action="store_true", help="Buchungen danach nicht vergleichen")
    ap.add_argument("--seed", type=int, help="Zufallswert (gleiche Ersatzwerte bei jedem Lauf)")
    args = ap.parse_args()

    src_root = args.folder.expanduser().resolve()
    if not src_root.is_dir():
        ap.error(f"Ordner nicht gefunden: {src_root}")
    dst_root = (args.output or src_root.with_name(src_root.name + "_anonym")).expanduser().resolve()
    if dst_root == src_root or src_root in dst_root.parents:
        ap.error("Zielordner darf nicht im Original-Ordner liegen")

    anon = Anonymizer(seed=args.seed)
    ask_personal_data(anon, args)

    # versteckte Ordner (z. B. .venv) überspringen
    files = sorted(p for p in src_root.rglob("*")
                   if p.is_file() and not any(part.startswith(".") for part in p.relative_to(src_root).parts)
                   and p.suffix.lower() in {".pdf", ".csv", *IMAGE_EXT})
    if not files:
        print("Keine PDFs, Bilder oder CSVs gefunden.")
        return 1
    missing = check_dependencies(files)
    if missing:
        packages = " ".join(m for m in missing if not m.startswith("tesseract"))
        brew = "    brew install tesseract tesseract-lang\n" if any(m.startswith("tesseract") for m in missing) else ""
        print(INSTALL_HELP.format(missing=", ".join(missing), python=sys.executable,
                                  packages=packages or "-r requirements.txt", brew=brew))
        return 1

    problems = 0
    done: list[tuple[Path, Path]] = []
    for src in files:
        dst = target_path(src, src_root, dst_root, anon)
        name = dst.relative_to(dst_root)   # nur anonymisierte Namen ausgeben
        ext = src.suffix.lower()
        try:
            if ext == ".pdf":
                warnings = anonymize_pdf(src, dst, anon)
            elif ext in IMAGE_EXT:
                warnings = anonymize_image(src, dst, anon)
            else:
                warnings = anonymize_text_file(src, dst, anon)
        except ImportError as e:
            print(f"  ✗ {name}: fehlendes Paket ({e.name})")
            problems += 1
            continue
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            problems += 1
            continue
        serious = [w for w in warnings if w.startswith(("NOCH", "Seite"))]
        problems += bool(serious)
        print(f"  {'!' if serious else '✓'} {name}")
        for w in warnings:
            print(f"      {w}")
        done.append((src, dst))

    print(f"\n{len(anon.originals)} verschiedene Werte ersetzt, Ergebnis in: {dst_root}")
    if not args.no_check and done:
        parse_check(done, dst_root)
    if problems:
        print(f"\n⚠ {problems} Datei(en) mit Hinweisen – diese bitte nicht ungeprüft verschicken.")
    else:
        print("\nKeine Originalwerte mehr gefunden. Bitte trotzdem 1–2 Dateien kurz ansehen.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
