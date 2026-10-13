# Finanzen – Einnahmen & Ausgaben auf dem iPhone

Zwei Teile:

| Teil | Wo läuft es? | Was macht es? |
|---|---|---|
| `importer/` | auf deinem Mac/PC | liest den Ordner mit Kontoauszügen (PDF), Screenshots und PayPal-Exporten und erzeugt `transactions.json` |
| `app/` | auf dem iPhone (Web-App) | zeigt Einnahmen, Ausgaben, Saldo, 12-Monats-Verlauf und Ausgaben nach Kategorie; Buchungen bearbeiten und manuell hinzufügen |

**Datenschutz:** Deine Finanzdaten landen nie im Repository oder auf einem Server.
Die App speichert alles nur lokal auf dem iPhone. `.gitignore` schließt PDFs, Bilder
und `transactions.*` aus.

---

## 1. Auszüge einlesen (Mac/PC)

```bash
cd importer
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Für Screenshots zusätzlich Tesseract (OCR) installieren:
#   macOS:   brew install tesseract tesseract-lang
#   Windows: https://github.com/UB-Mannheim/tesseract/wiki  (Deutsch mit auswählen)

python3 import_statements.py ~/Pfad/zum/Ordner -o transactions.json
```

### Ordnerstruktur (empfohlen)

Leg pro Konto einen Unterordner an. Am Ordnernamen erkennt der Importer, wie der Auszug zu lesen ist:

```
Finanzen/
├── Postbank/         Kontoauszüge als PDF
├── easybank/         Kreditkartenabrechnungen als PDF
├── Trade Republic/   Kontoauszüge (Umsatzübersicht) als PDF
└── PayPal/           Screenshots aus der PayPal-App (oder CSV-Export)
```

| Quelle | Besonderheit, die der Importer berücksichtigt |
|---|---|
| Postbank | Datum `02.03./02.03.`, Beträge `- 64,00` / `+ 2.500,00` |
| easybank Kreditkarte | Käufe meist ohne Vorzeichen → werden als Ausgabe gezählt, Gutschriften als Einnahme |
| Trade Republic | Datum `01 Okt. 2024`, Eingang/Ausgang ohne Vorzeichen → Richtung aus der Saldo-Änderung |
| PayPal-Screenshots | Datum `2. Mai` ohne Jahr → Jahr aus dem Datum des Screenshots; Bankgutschriften werden übersprungen |

**Umbuchungen zwischen deinen Konten** (Postbank → PayPal, Postbank → Trade Republic,
Kreditkartenabrechnung) bekommen die Kategorie *Umbuchung* und zählen nicht doppelt.
Damit das stimmt, sollten die PayPal-Screenshots den gleichen Zeitraum abdecken wie die Bankauszüge.

Ausgabe pro Datei, z. B.:

```
    23 Buchungen  Kontoauszug_2026_03.pdf
    41 Buchungen  paypal_2026.csv
     0 Buchungen  IMG_1234.png   <- nichts erkannt, mit --debug prüfen
```

Zusätzlich entsteht `transactions.csv` (Excel/Numbers) zum Prüfen.

### Tipps

- **PayPal:** Screenshots funktionieren. Genauer ist aber der offizielle Export:
  paypal.com → *Aktivitäten* → *Kontoauszüge* → *Benutzerdefiniert* → **CSV**.
  Das ist exakt und braucht keine Texterkennung.
- **Doppelte Buchungen:** Ein Umsatz, der als PDF *und* als Screenshot vorliegt, wird nur einmal gezählt.
- **PayPal-Abbuchungen auf dem Girokonto** bekommen die Kategorie *Umbuchung* und zählen nicht als Ausgabe,
  weil der eigentliche Einkauf schon in den PayPal-Daten steht.
- **Kategorien** stehen in `importer/categories.json` (Kategorie → Suchbegriffe, erste passende gewinnt).
- **Nichts erkannt?** `--debug` zeigt den gelesenen Text. Jede Bank formatiert anders – mit dem
  Rohtext lässt sich der Parser in `parse.py` an deine Bank anpassen.

### Auszüge anonymisieren (zum Teilen)

Um Beispieldateien weiterzugeben, ohne persönliche Daten preiszugeben:

```bash
python3 importer/anonymize.py ~/Finanzen
```

Das Skript fragt nach deinem Namen, deiner Adresse und weiteren Namen und schreibt
anonymisierte Kopien nach `~/Finanzen_anonym/` (gleiche Ordnerstruktur, Originale bleiben unverändert).

- **Ersetzt:** deine Angaben sowie automatisch IBANs, Kartennummern, Kunden-, Mandats- und
  Referenznummern, Gläubiger-IDs, E-Mail-Adressen und Telefonnummern. Gleiche Werte bekommen
  überall denselben Ersatz, und die Ersatz-IBANs haben gültige Prüfziffern.
- **Bleibt:** Datum, Beträge und Händlernamen, damit sich die Dateien weiter einlesen lassen.
- **Prüft danach:** ob noch ein Originalwert in den Dateien steht und ob der Importer vorher und
  nachher dieselben Buchungen erkennt.
- PDFs werden *echt* geschwärzt: Der Originaltext wird entfernt, nicht nur überdeckt, und die
  Metadaten werden geleert.
- Screenshots laufen über Texterkennung. Die kann einzelne Wörter übersehen, also die Bilder
  vor dem Verschicken kurz ansehen. Eingescannte PDFs ohne Text werden gemeldet und nicht anonymisiert.

## 2. App aufs iPhone

Die App ist eine *Progressive Web App*: kein App Store, kein Xcode nötig.

1. **Veröffentlichen** (einmalig): Auf GitHub unter *Settings → Pages → Source* **GitHub Actions** wählen
   und den Branch nach `main` mergen. Der Workflow `.github/workflows/pages.yml` stellt den Ordner `app/`
   unter `https://<benutzer>.github.io/<repo>/` bereit.
   *(Bei privaten Repos braucht GitHub Pages einen bezahlten Plan. Alternative: den Ordner `app/`
   kostenlos per Drag & Drop bei Netlify Drop oder Cloudflare Pages hochladen.)*
2. Die URL in **Safari** auf dem iPhone öffnen → Teilen-Symbol → **Zum Home-Bildschirm**.
3. `transactions.json` per AirDrop oder iCloud Drive aufs iPhone legen.
4. In der App: **Import → Datei wählen**.

Neue Monate einfach erneut importieren: Bereits vorhandene Buchungen werden übersprungen,
deine Änderungen an Kategorien bleiben erhalten.

### Funktionen

- Monats- und Jahresansicht mit Einnahmen, Ausgaben, Saldo und Sparquote
- 12-Monats-Verlauf (antippen zeigt Details und springt zum Monat)
- Ausgaben nach Kategorie → antippen öffnet die passenden Buchungen
- Suche und Filter, Buchungen bearbeiten, löschen und manuell hinzufügen
- „Kategorie für ähnliche Buchungen übernehmen“ merkt sich die Zuordnung auch für künftige Importe
- Backup-Export als JSON, funktioniert offline, Hell- und Dunkelmodus

### Lokal ausprobieren

```bash
cd app && python3 -m http.server 8000
# -> http://localhost:8000
```

## Tests

```bash
cd importer && python3 -m pytest -q
```
